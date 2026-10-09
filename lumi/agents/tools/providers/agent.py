"""Agent 工具提供者 - 将复杂任务委托给子代理执行。"""

# 注意：本模块**不能**加 `from __future__ import annotations`。它会把 `runtime: ToolRuntime`
# 注解字符串化，导致 langchain 在工具调用时认不出该注入参数、不注入 → "missing runtime"。
# 任何声明 `runtime: ToolRuntime` 注入参数的工具模块同理（见回归测试 test_runtime_injected_via_toolnode）。

import asyncio
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.core.meta_message import extract_text_content
from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    bg_tasks_dir,
    get_task_registry,
    make_bg_done_callback,
    new_task_id,
    run_background_task,
)
from lumi.agents.runtime.shell_session import run_with_shell
from lumi.utils.config import get_config

_AGENT_DESCRIPTION = """启动一个专门的子代理（独立上下文）来自主完成复杂任务。每种代理类型都具备特定的能力和可用工具。

## 如何调用：
- 用本工具并指定 name（代理名称）与 prompt（交给它的任务描述）

## 何时使用：
- 当任务匹配可用的代理类型、有可并行运行的独立工作，或回答问题需要跨多个文件查阅时，请使用此功能
- 委托处理后，你将获得结论，而非原始文件内容。若只需查找单一事实且已知具体文件、符号或值，请直接搜索
- 一旦委托就不要再自行执行，请等待结果返回
- 多个互不依赖的任务应在同一条消息里一次性并行派出，不要派一个等一个

## 何时不用：
- 单点查询或你自己两三步就能完成的事——直接做比派代理更快

## 注意事项：
- 可用代理列表会在对话中的 `<system-reminder>` 里给出，且随项目动态变化——始终以最新列表为准，列表之外的代理无法调用
- 子代理默认在后台运行，完成时会通知你
- 仅当单个子任务的结果是继续推进的唯一前提，才传 run_in_background=false 以同步方式运行
- 子代理的最终消息将作为工具结果返回给你，不会展示给用户——请转达关键信息"""


async def create_subagent(
    parent,
    tools: list,
    *,
    system_prompt: str | None = None,
    model_name: str | None = None,
    depth: int = 1,
    interactive: bool = False,
):
    """子代理的唯一构建入口（agent 工具与 workflow 共用；工具集由调用方按各自规则选好）。

    复用父 PermissionEngine 与项目根（共享工作区边界）；不持久化、不带持久记忆
    （临时执行单元保持上下文干净，项目说明 LUMI.md 仍由 preprocess 注入）；<env> 的
    渠道条目随父传播。返回的 run(prompt, schema) 统一构造输入、传播审批上下文，
    并在独立 shell 中执行；可经 on_progress 接收每步状态。
    """
    from lumi.agents.core.graph import create_agent

    lumi_agent, context = await create_agent(
        tools=tools,
        system_prompt=system_prompt,
        model_name=model_name,
        permission_engine=parent.permission_engine,
        project_dir=parent.project_dir,
        enable_memory=False,
    )
    context.env_extra = parent.env_extra
    if interactive:
        context.approval_broker = parent.approval_broker
        context.widen_boundary = parent.widen_boundary
        context.mode_parent = parent

    async def run(prompt: str, schema: dict | None = None, *, on_progress=None) -> dict:
        inputs = {"messages": [HumanMessage(content=prompt)], "depth": depth}
        if schema is not None:
            inputs["output_schema"] = schema

        async def execute() -> dict:
            if on_progress is None:
                return await lumi_agent.graph.ainvoke(inputs, context=context)
            final: dict = {}
            async for state in lumi_agent.graph.astream(
                inputs, context=context, stream_mode="values"
            ):
                final = state
                on_progress(state)
            return final

        return await run_with_shell(new_task_id("sub-"), execute())

    return run


def _child_tools(all_tools: list, child_depth: int, max_depth: int) -> list:
    """子代理工具集：不含 ask（向用户提问只归主 agent——IM 渠道无处作答、后台无人应答）；
    未达委派上限保留 agent / workflow（可继续往下委派），否则剔除以防无限递归。"""
    excluded = {"ask"} if child_depth < max_depth else {"ask", "agent", "workflow"}
    return [t for t in all_tools if t.name not in excluded]


class AgentInput(BaseModel):
    """Agent 工具的输入参数"""

    name: str = Field(description="用于此任务的 agent 名称")
    prompt: str = Field(description="交给 agent 执行的任务的描述")
    run_in_background: bool = Field(
        default=True,
        description="默认后台运行，完成后自动收到含结果的通知；仅当该结果是继续推进的唯一前提且期间无事可做时，才设为 false 以同步方式运行",
    )


@tool(description=_AGENT_DESCRIPTION, args_schema=AgentInput)
async def agent(
    name: str,
    prompt: str,
    runtime: ToolRuntime,
    run_in_background: bool = True,
) -> str:
    """Agent工具 - 委托给 LumiAgent 执行"""

    # 委派深度网关：当前 agent 已达上限则拒绝再委派（主 agent depth=0）
    current_depth: int = runtime.state.get("depth", 0)
    max_depth: int = get_config().config.agents.max_delegation_depth
    if current_depth >= max_depth:
        return f"已达到最大委派层数（{max_depth}），无法再委派子代理"
    child_depth = current_depth + 1

    # 走按项目缓存的 detector（与上下文注入的 agent 列表同源），文件未变不重解析
    from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector

    agent_configs = AgentChangeDetector.get_instance(runtime.context.project_dir).peek()
    agent_config = next((a for a in agent_configs if a.name == name), None)
    if agent_config is None:
        return f"Agent '{name}' not found"

    # 子代理工具：未达上限保留 agent 工具（可继续委派），到顶则剔除。
    # 项目随父 context 显式传递；get_tools 默认等冷池就位（子代理无轮首刷新可自愈）。
    # lazy import：providers 不能顶层引 tools 包（包 __init__ 反向注册本模块，成环）
    from lumi.agents.tools import get_tools

    all_tools = await get_tools(
        tools=agent_config.tools or None, project_dir=runtime.context.project_dir
    )
    run = await create_subagent(
        runtime.context,
        _child_tools(all_tools, child_depth, max_depth),
        system_prompt=agent_config.system_prompt,
        model_name=agent_config.model or None,
        depth=child_depth,
        interactive=not run_in_background,
    )

    if run_in_background:
        return _start_background_agent(name, prompt, run)

    invoke_result = await run(prompt)

    content = invoke_result["messages"][-1].content if invoke_result["messages"] else ""
    return extract_text_content(content)


# ---------------------------------------------------------------------------
# Background agent helpers
# ---------------------------------------------------------------------------


def _start_background_agent(
    name: str,
    prompt: str,
    run,
) -> str:
    """注册后台 Agent 任务并 fire-and-forget 启动。"""
    task_id = new_task_id("bg_")

    output_file = bg_tasks_dir() / f"{task_id}.txt"

    entry = BackgroundTaskEntry(
        task_id=task_id,
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label=f"agent:{name}",
        started_at=time.time(),
        output_file=output_file,
        agent_name=name,
        prompt=prompt,
    )

    registry = get_task_registry()
    registry.register(entry)

    async_task = asyncio.create_task(
        _run_agent_background(task_id, run, prompt, output_file)
    )
    entry.async_task = async_task
    async_task.add_done_callback(make_bg_done_callback(task_id, "agent bg"))

    return (
        f"后台代理任务已启动\n"
        f"Task ID: {task_id}\n"
        f"Agent: {name}\n"
        f"Output File: {output_file.resolve()}\n"
        f"\n"
        f"完成时你会自动收到通知（含结果）。在此之前**不要**轮询状态或读取 Output File，"
        f"等通知即可——期间请继续做别的事。\n"
    )


def _agent_activity(state: dict, tools_done: int = 0) -> dict:
    """从状态快照抽后台代理的活动摘要（drawer 卡片的「它正在做什么」）。

    后台代理的 output_file 完成时才写，运行中唯一能看的就是这个：``tool`` 为当前发起
    的工具调用（模型思考中 / 刚收完工具结果时为 None），``tools_done`` 为已完成的工具数。

    计数取 ``max(已知值, 本快照所见)``：Summarizer 压缩会 Overwrite 整段替换掉历史，
    只数当前快照的话，长任务压缩后计数会当着用户的面往回跳。
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    calls = getattr(last, "tool_calls", None) or []
    return {
        "tool": ", ".join(c["name"] for c in calls) or None,
        "tools_done": max(
            tools_done, sum(1 for m in messages if isinstance(m, ToolMessage))
        ),
    }


async def _run_agent_background(
    task_id: str,
    run,
    prompt: str,
    output_file: Path,
) -> None:
    """后台执行 Agent；收尾（写文件 / 状态 / 通知）走共用 run_background_task。"""
    registry = get_task_registry()

    activity: dict = {}

    def progress(state: dict) -> None:
        nonlocal activity
        activity = _agent_activity(state, activity.get("tools_done", 0))
        registry.notify_progress(task_id, activity)

    async def execute() -> str:
        final = await run(prompt, on_progress=progress)
        msgs = final.get("messages") or []
        return extract_text_content(msgs[-1].content if msgs else "")

    await run_background_task(task_id, output_file, execute, cancel_text="任务被取消")

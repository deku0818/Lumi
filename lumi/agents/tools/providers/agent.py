"""Create, discover and continue persistent subagent sessions; workflow keeps its temporary runner."""

# ToolRuntime must remain a concrete annotation for LangChain's parameter injection.
import asyncio
import json
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.runtime.bg_tasks import current_thread_id, new_task_id
from lumi.agents.runtime.shell_session import run_with_shell
from lumi.agents.runtime.subagents import get_subagent_service
from lumi.utils.config import get_config


async def create_subagent(
    parent,
    tools: list,
    *,
    system_prompt: str | None = None,
    model_name: str | None = None,
    depth: int = 1,
    interactive: bool = False,
):
    """Workflow 等临时执行单元的构建入口；持久 subagent 由运行时执行器管理。

    复用父 PermissionEngine 与项目根（共享权限规则）；不持久化、不带持久记忆
    （临时执行单元保持上下文干净，AGENTS.md 仍按系统提示词规则按需读取）；<env> 的
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


def _subagent_tools(all_tools: list, subagent_depth: int, max_depth: int) -> list:
    """持久子代理的 ask 由执行器转交主 Agent，不直接向用户发问；
    未达委派上限保留 agent_create / workflow（可继续往下委派），否则剔除以防无限递归。"""
    excluded = {"agent_create", "workflow"} if subagent_depth >= max_depth else set()
    return [t for t in all_tools if t.name not in excluded]


class AgentCreateInput(BaseModel):
    name: str | None = Field(
        default=None,
        description="可用 Agent 名称见 system-reminder；省略使用默认子代理",
    )
    cwd: str | None = Field(
        default=None,
        min_length=1,
        description="实例工作目录；默认继承主会话当前目录，相对路径基于该目录解析，续聊保持不变",
    )
    prompt: str = Field(min_length=1, description="第一轮任务：目标、背景和期望产出")
    run_in_background: bool = Field(
        default=True, description="默认后台执行并自动回传结果；false 等待本轮结果"
    )


class AgentMessageInput(BaseModel):
    agent_id: str = Field(description="agent_create 或 agent_list 返回的稳定实例 ID")
    prompt: str = Field(
        min_length=1, description="发给原子会话的新任务、补查要求或问题的答案"
    )
    run_in_background: bool = Field(
        default=True, description="默认后台执行；已有任务运行时排队，不并发执行两轮"
    )


def _parent_id(runtime: ToolRuntime) -> str:
    config = getattr(runtime, "config", None) or {}
    return config.get("configurable", {}).get("thread_id") or current_thread_id.get()


def _working_directory(runtime: ToolRuntime, cwd: str | None) -> Path:
    context = runtime.context
    project = context.project_dir
    if project is None and context.permission_engine is not None:
        project = context.permission_engine.project_dir
    base = Path(project or Path.cwd()).resolve()
    directory = Path(cwd).expanduser() if cwd is not None else base
    return (base / directory).resolve()


def _request_key(runtime: ToolRuntime, parent_id: str, operation: str) -> str | None:
    call_id = getattr(runtime, "tool_call_id", None)
    return f"{parent_id}:{operation}:{call_id}" if call_id else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


@tool(
    args_schema=AgentCreateInput,
    description="""创建独立上下文的可持续子代理并派出第一轮任务。
可用 Agent 名称和能力见 system-reminder；省略 name 时使用默认子代理。
cwd 默认继承主会话当前目录，可显式指定其他目录；目录绑定实例，后续 agent_message 沿用。
实例归属当前主会话，主会话切换工作目录不影响查询、续聊和结果回传。
返回稳定 agent_id 和本轮 turn_id/task_id。默认后台运行，完成自动收到对应轮次的结果；
期间继续其他工作，不要轮询。需要追问时用 agent_message 联系原实例，不要重复创建。
仅当该结果是继续工作的唯一前提且期间无事可做时，设 run_in_background=false。
子代理看不到主对话，prompt 应写清目标与背景。只做两三步的单点查询请直接执行。""",
)
async def agent_create(
    prompt: str,
    runtime: ToolRuntime,
    name: str | None = None,
    cwd: str | None = None,
    run_in_background: bool = True,
) -> str:
    """Create a named subagent instance; new instance identity is independent of its first task."""
    from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector

    depth = runtime.state.get("depth", 0)
    maximum = get_config().config.agents.max_delegation_depth
    if depth >= maximum:
        return f"已达到最大委派层数（{maximum}），无法再委派子代理"
    parent_id = _parent_id(runtime)
    try:
        definition = {"name": "lumi", "executor": "lumi", "system_prompt": ""}
        if name:
            configs = await asyncio.to_thread(
                AgentChangeDetector.get_instance(runtime.context.project_dir).peek
            )
            profile = next((config for config in configs if config.name == name), None)
            if profile is None:
                raise ValueError(
                    f"Agent '{name}' 未启用或不可用，请查看 system-reminder 中的可用清单"
                )
            definition = profile.model_dump()
        working_dir = _working_directory(runtime, cwd)
        service = get_subagent_service()
        receipt = service.create(
            parent_id,
            str(working_dir),
            definition,
            depth + 1,
            prompt,
            notify=run_in_background,
            request_key=_request_key(runtime, parent_id, "create"),
            env_extra=runtime.context.env_extra,
            tool_mode=runtime.context.tool_mode,
        )
        if not run_in_background:
            receipt = await service.wait(receipt["turn_id"])
        return _json(receipt)
    except (ValueError, RuntimeError) as error:
        return _json({"error": str(error)})


@tool(
    description="""列出当前会话已创建的子代理实例（不是可创建的配置类型）。
返回稳定 agent_id、执行器、任务状态、最近轮次和结果摘要。只读取元数据，不启动执行器。
用于重新定位已有实例、压缩上下文后恢复联系；完成会自动通知，不要反复轮询等待。"""
)
async def agent_list(runtime: ToolRuntime) -> str:
    """Read subagents owned by the caller, without invoking a model or restoring a process."""
    parent_id = _parent_id(runtime)
    if not parent_id:
        return _json({"error": "需要当前会话 thread_id"})
    return _json({"agents": get_subagent_service().list_agents(parent_id)})


@tool(
    args_schema=AgentMessageInput,
    description="""向已有子代理继续发送消息，保留它此前的上下文。
agent_id 必须来自当前会话的 agent_create 或 agent_list。
每次消息分配独立 turn_id；原子代理正在运行时排队，空闲后执行。
默认后台运行并自动回传本轮结果，不要轮询。不需要重新创建实例。
结果包含 questions 时先依据主会话上下文自行回答；确需用户决策才用 ask，获得答案后
用 agent_message 将问题及答案发给同一 agent_id，继续原任务，不要重新创建实例。
仅当结果是继续推进的唯一前提时设 run_in_background=false，等待本轮结果。""",
)
async def agent_message(
    agent_id: str,
    prompt: str,
    runtime: ToolRuntime,
    run_in_background: bool = True,
) -> str:
    """Submit another FIFO turn to an owned subagent instance."""
    parent_id = _parent_id(runtime)
    try:
        service = get_subagent_service()
        receipt = service.message(
            parent_id,
            agent_id,
            prompt,
            notify=run_in_background,
            request_key=_request_key(runtime, parent_id, "message"),
        )
        if not run_in_background:
            receipt = await service.wait(receipt["turn_id"])
        return _json(receipt)
    except (ValueError, RuntimeError) as error:
        return _json({"error": str(error)})

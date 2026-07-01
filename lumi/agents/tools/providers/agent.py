"""Agent 工具提供者 - 将复杂任务委托给子代理执行。"""

# 注意：本模块**不能**加 `from __future__ import annotations`。它会把 `runtime: ToolRuntime`
# 注解字符串化，导致 langchain 在工具调用时认不出该注入参数、不注入 → "missing runtime"。
# 任何声明 `runtime: ToolRuntime` 注入参数的工具模块同理（见回归测试 test_runtime_injected_via_toolnode）。

import asyncio
import time
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    bg_tasks_dir,
    get_task_registry,
    make_bg_done_callback,
    run_background_task,
)
from lumi.agents.runtime.shell_session import run_with_shell
from lumi.agents.tools.loader import load_agents
from lumi.agents.tools.registry import get_tool_registry
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

_TASK_ID_HEX_LENGTH = 12

_AGENT_DESCRIPTION = """启动一个专门的子代理（独立上下文）来自主完成复杂任务。每种代理类型都具备特定的能力和可用工具。

如何调用：
- 用本工具并指定 name（代理名称）与 prompt（交给它的任务描述）

注意事项：
- 可用代理列表会在对话中的 `<system-reminder>` 里给出，且随项目动态变化——始终以最新列表为准，列表之外的代理无法调用
- 子代理可继续委派下层子代理，但嵌套层数有上限；达到上限后将无法再委派"""


def _child_tools(all_tools: list, child_depth: int, max_depth: int) -> list:
    """子代理工具集：未达委派上限则保留 agent 工具（可继续往下委派），否则剔除以防无限递归。"""
    if child_depth >= max_depth:
        return [t for t in all_tools if t.name != "agent"]
    return list(all_tools)


class AgentInput(BaseModel):
    """Agent 工具的输入参数"""

    name: str = Field(description="用于此任务的 agent 名称")
    prompt: str = Field(description="交给 agent 执行的任务的描述")
    run_in_background: bool = Field(
        default=False, description="设为 true 可在后台运行，完成后会收到通知"
    )


@tool(description=_AGENT_DESCRIPTION, args_schema=AgentInput)
async def agent(
    name: str,
    prompt: str,
    runtime: ToolRuntime,
    run_in_background: bool = False,
) -> str:
    """Agent工具 - 委托给 LumiAgent 执行"""
    # Lazy import 避免循环依赖
    from lumi.agents.core.graph import create_agent
    from lumi.agents.core.response import extract_ainvoke_content

    # 委派深度网关：当前 agent 已达上限则拒绝再委派（主 agent depth=0）
    current_depth: int = runtime.state.get("depth", 0)
    max_depth: int = get_config().config.agents.max_delegation_depth
    if current_depth >= max_depth:
        return f"已达到最大委派层数（{max_depth}），无法再委派子代理"
    child_depth = current_depth + 1

    matched_configs = load_agents(name=name)
    if not matched_configs:
        return f"Agent '{name}' not found"

    agent_config = matched_configs[0]

    # 子代理工具：未达上限保留 agent 工具（可继续委派），到顶则剔除
    all_tools = await get_tool_registry().get_tools(
        names=agent_config.tools or None,
    )
    available_tools = _child_tools(all_tools, child_depth, max_depth)

    # 创建并执行 agent（子 agent 不使用 checkpointer，复用主 agent 权限引擎）
    # enable_memory=False：子 agent 是临时执行单元，不注入持久记忆（MEMORY.md +
    # 行为说明），保持上下文干净；项目说明 LUMI.md 仍由 preprocess 注入。
    lumi_agent, context = await create_agent(
        tools=available_tools,
        system_prompt=agent_config.system_prompt,
        model_name=agent_config.model or None,
        permission_engine=runtime.context.permission_engine,
        enable_memory=False,
    )

    if run_in_background:
        return _start_background_agent(name, prompt, lumi_agent, context, child_depth)

    # 前台同步执行路径：传播在途审批 Broker，子代理审批经父流的 astream_events 浮现，
    # 白嫖 custom event 自带的 parent_ids 归属到本子代理卡片（旧 interrupt 无 checkpointer
    # 不可用，broker 才解锁子代理审批）。后台子代理 detached、无活流可挂，刻意不传播。
    context.approval_broker = runtime.context.approval_broker
    # tool_mode 是 context 属性：从父 context 继承实时值（父运行中切换的模式随之传播）
    context.tool_mode = runtime.context.tool_mode
    logger.debug("[agent tool] resolved tool_mode=%s", context.tool_mode)
    inputs = {
        "messages": [HumanMessage(content=prompt)],
        "depth": child_depth,
    }
    # 子代理独立 shell：cd/env 不污染父与兄弟代理；用完即回收
    sub_key = f"sub-{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"
    invoke_result = await run_with_shell(
        sub_key, lumi_agent.graph.ainvoke(inputs, context=context)
    )

    content = invoke_result["messages"][-1].content if invoke_result["messages"] else ""
    return extract_ainvoke_content(content)


# ---------------------------------------------------------------------------
# Background agent helpers
# ---------------------------------------------------------------------------


def _start_background_agent(
    name: str,
    prompt: str,
    lumi_agent,
    context,
    depth: int,
) -> str:
    """注册后台 Agent 任务并 fire-and-forget 启动。"""
    task_id = f"bg_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"

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

    # 后台子 agent 无交互审批通道，固定 privileged（tool_mode 是 context 属性）
    context.tool_mode = "privileged"
    inputs = {
        "messages": [HumanMessage(content=prompt)],
        "depth": depth,
    }
    async_task = asyncio.create_task(
        _run_agent_background(task_id, lumi_agent, context, inputs, output_file)
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


async def _run_agent_background(
    task_id: str,
    lumi_agent,
    context,
    inputs: dict,
    output_file: Path,
) -> None:
    """后台执行 Agent；收尾（写文件 / 状态 / 通知）走共用 run_background_task。"""
    from lumi.agents.core.response import extract_ainvoke_content

    async def _produce() -> str:
        # 后台子代理独立 shell（键用 task_id，已唯一）：与父/兄弟隔离、用完回收
        invoke_result = await run_with_shell(
            task_id, lumi_agent.graph.ainvoke(inputs, context=context)
        )
        msgs = invoke_result["messages"]
        return extract_ainvoke_content(msgs[-1].content if msgs else "")

    await run_background_task(task_id, output_file, _produce, cancel_text="任务被取消")

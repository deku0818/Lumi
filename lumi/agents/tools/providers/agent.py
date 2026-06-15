"""Agent 工具提供者 - 将复杂任务委托给子代理执行。"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from lumi.agents.tools.loader import AgentConfig, load_agents
from lumi.agents.tools.registry import get_tool_registry
from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    get_task_registry,
    make_bg_done_callback,
    run_background_task,
)
from lumi.utils.logger import logger

_BG_TASKS_DIR = ".lumi/bg_tasks"
_TASK_ID_HEX_LENGTH = 12

_AGENT_EXAMPLES = """使用示例：
<example_agent_descriptions>
"test-runner"：在完成代码编写后，使用此代理运行测试
"greeting-responder"：使用此代理以友好的笑话回应用户问候
</example_agent_descriptions>

<example>
用户："请写一个函数来检查一个数是否为质数"
助手：我将使用 write 工具编写以下代码：
<code>
function isPrime(n) {
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {
    if (n % i === 0) return false
  }
  return true
}
</code>
<commentary>
由于已编写了一段重要代码且任务已完成，现在使用 test-runner 代理运行测试
</commentary>
助手：使用 agent 工具启动 test-runner 代理
</example>

<example>
用户："你好"
<commentary>
由于用户正在打招呼，使用 greeting-responder 代理以友好的笑话进行回应
</commentary>
助手："我将使用 Agent 工具启动 greeting-responder 代理"
</example>"""


def _build_agent_description(agents: list[AgentConfig]) -> str:
    """根据已注册的代理列表生成工具描述文本。"""
    agent_list = "\n".join(f"- {a.name}：{a.description}" for a in agents)
    return (
        "agent 工具会启动专门的代理（子进程），这些代理可自主处理复杂任务。"
        "每种代理类型都具备特定的能力和可用工具。\n"
        f"可用的代理：\n{agent_list}\n\n"
        f"{_AGENT_EXAMPLES}"
    )


def _create_agent_schema(
    agents: list[AgentConfig] | None = None,
) -> tuple[str, dict[str, Any]]:
    """动态创建 agent 工具的 description 和 JSON schema。"""
    if agents is None:
        agents = load_agents()
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": [a.name for a in agents],
                "description": "用于此任务的agent名称",
            },
            "prompt": {
                "type": "string",
                "description": "交给agent执行的任务的描述",
                "default": "",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "设为 true 可在后台运行，完成后会收到通知",
                "default": False,
            },
        },
        "required": ["name", "prompt"],
    }
    return _build_agent_description(agents), schema


def _init_schema(agents: list[AgentConfig]) -> None:
    """由外部调用，传入已加载的 agents 列表，更新 tool 对象的描述和 schema。"""
    description, schema = _create_agent_schema(agents)
    # 更新已装饰的 tool 对象属性（@tool 装饰器在导入时已用空值创建）
    agent.description = description
    agent.args_schema = schema


# 初始描述/schema 为空占位符，由 _init_schema() 在启动时填充
@tool(description="", args_schema={})
async def agent(
    name: str,
    prompt: str,
    runtime: ToolRuntime,
    run_in_background: bool = False,
) -> str:
    """Agent工具 - 委托给 LumiAgent 执行"""
    # Lazy import 避免循环依赖
    from lumi.agents.core.response import extract_ainvoke_content
    from lumi.agents.core.graph import create_agent

    matched_configs = load_agents(name=name)
    if not matched_configs:
        return f"Agent '{name}' not found"

    agent_config = matched_configs[0]

    # 获取工具（排除 agent 工具自身以避免递归）
    all_tools = await get_tool_registry().get_tools(
        names=agent_config.tools or None,
    )
    available_tools = [t for t in all_tools if t.name != "agent"]

    # 创建并执行 agent（子 agent 不使用 checkpointer，复用主 agent 权限引擎）
    lumi_agent, context = await create_agent(
        tools=available_tools,
        system_prompt=agent_config.system_prompt,
        model_name=agent_config.model or None,
        permission_engine=runtime.context.permission_engine,
    )

    if run_in_background:
        return _start_background_agent(name, prompt, lumi_agent, context)

    # 前台同步执行路径（不变）
    tool_mode: str = runtime.state.get("tool_mode", "default")
    logger.debug("[agent tool] resolved tool_mode=%s", tool_mode)
    inputs = {"messages": [HumanMessage(content=prompt)], "tool_mode": tool_mode}
    invoke_result = await lumi_agent.graph.ainvoke(inputs, context=context)

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
) -> str:
    """注册后台 Agent 任务并 fire-and-forget 启动。"""
    task_id = f"bg_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"

    from lumi.agents.permissions.workspace import get_authorized_directory

    output_dir = Path(str(get_authorized_directory())) / _BG_TASKS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{task_id}.txt"

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

    inputs = {"messages": [HumanMessage(content=prompt)], "tool_mode": "privileged"}
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
        invoke_result = await lumi_agent.graph.ainvoke(inputs, context=context)
        msgs = invoke_result["messages"]
        return extract_ainvoke_content(msgs[-1].content if msgs else "")

    await run_background_task(task_id, output_file, _produce, cancel_text="任务被取消")

"""Agent 工具提供者 - 将复杂任务委托给子代理执行。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from lumi.agents.tools.loader import AgentConfig, load_agents
from lumi.agents.tools.registry import ToolRegistry
from lumi.utils.logger import logger

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
    all_tools = await ToolRegistry.instance().get_tools(
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

    tool_mode: str = runtime.state.get("tool_mode", "auto")
    logger.debug("[agent tool] resolved tool_mode=%s", tool_mode)
    inputs = {"messages": [HumanMessage(content=prompt)], "tool_mode": tool_mode}
    invoke_result = await lumi_agent.graph.ainvoke(inputs, context=context)

    content = invoke_result["messages"][-1].content if invoke_result["messages"] else ""
    return extract_ainvoke_content(content)

"""Agent工具提供者 - 提供任务委托工具

将复杂任务委托给子代理执行。使用 LumiAgent 替代 OmniAgent 的 SimpleAgent。
"""

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from lumi.agents.tools.config import load_agents
from lumi.agents.tools.registry import ToolRegistry


def _create_agent_schema():
    """动态创建agent工具的schema"""
    agents = load_agents()
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": [a.name for a in agents],
                "description": "代理名称",
            },
            "prompt": {
                "type": "string",
                "description": "交给代理执行的任务的描述",
                "default": "",
            },
        },
        "required": ["name", "prompt"],
    }
    description = "启动一个新的代理来自主处理复杂的多步骤任务。\n"
    for a in agents:
        description += f"{a.name}：{a.description}\n"

    return description, schema


@tool(description=_create_agent_schema()[0], args_schema=_create_agent_schema()[1])
async def agent(name: str, prompt: str):
    """Agent工具 - 委托给 LumiAgent 执行"""
    # Lazy import避免循环依赖
    from lumi.agents.base.response_service import (
        extract_ainvoke_content,
    )
    from lumi.agents.core.graph import LumiAgent
    from lumi.agents.core.scheme import LumiAgentContext

    # 加载agent配置
    agent_configs = load_agents(name=name)
    if not agent_configs:
        return f"Agent '{name}' not found"

    agent_config = agent_configs[0]

    # 获取工具 (排除agent工具自身避免递归)
    registry = ToolRegistry.instance()
    all_tools = await registry.get_tools(
        names=agent_config.tools if agent_config.tools else None,
    )
    tools = [t for t in all_tools if t.name != "agent"]

    # 创建并执行agent
    lumi_agent = LumiAgent()

    context = LumiAgentContext(
        tools=tools,
        system_prompt=agent_config.system_prompt,
        model_name=agent_config.model or "",
    )

    inputs = {"messages": [HumanMessage(content=prompt)], "tool_mode": "auto"}
    result = await lumi_agent.graph.ainvoke(inputs, context=context)

    content = result["messages"][-1].content if result["messages"] else ""
    return extract_ainvoke_content(content)

"""工具系统 - 公共API"""

from langchain_core.tools.structured import StructuredTool

from lumi.utils.logger import logger

from .config import AgentConfig, SkillConfig, load_agents, load_skills

# 导入工具提供者模块
from .providers import ask, bash, cron, filesystem, mcp, todo
from .registry import ToolRegistry

# 注册常驻工具提供者
ToolRegistry.register("mcp", mcp.get_mcp_tools)
ToolRegistry.register("filesystem", filesystem)
ToolRegistry.register("bash", bash)
ToolRegistry.register("todo", todo)
ToolRegistry.register("ask", ask)
ToolRegistry.register("cron", cron)

# 条件注册：仅在有配置时才导入和注册
try:
    if load_agents():
        from .providers import agent

        ToolRegistry.register("agent", agent)
except Exception as e:
    logger.warning(f"加载 agent 配置失败，'agent' 工具不可用: {e}")

try:
    if load_skills():
        from .providers import skill

        ToolRegistry.register("skill", skill)
except Exception as e:
    logger.warning(f"加载 skill 配置失败，'skill' 工具不可用: {e}")


async def get_tools(
    tools: list[str] | None = None,
    disabled_tools: list[str] | None = None,
) -> list[StructuredTool]:
    """
    获取工具，支持白名单和黑名单过滤

    过滤逻辑：
    1. 如果 tools 为空 → 使用所有工具
    2. 如果 tools 非空 → 只使用 tools 中指定的工具
    3. 最后从结果中移除 disabled_tools 中的工具（优先级更高）

    Args:
        tools: 启用的工具列表（白名单），空列表或 None 表示启用所有工具
        disabled_tools: 要禁用的工具名称列表（黑名单）

    Example:
        tools = await get_tools()
        tools = await get_tools(tools=["read", "write"])
        tools = await get_tools(disabled_tools=["bash"])
        tools = await get_tools(tools=["read", "write"], disabled_tools=["write"])
    """
    all_tools = await ToolRegistry.instance().get_tools()

    # 白名单过滤
    if tools:
        all_tools = [t for t in all_tools if t.name in tools]

    # 黑名单过滤（优先级更高）
    if disabled_tools:
        all_tools = [t for t in all_tools if t.name not in disabled_tools]

    return all_tools


__all__ = [
    # 配置加载
    "AgentConfig",
    "SkillConfig",
    "load_agents",
    "load_skills",
    # 工具注册表
    "ToolRegistry",
    # 便捷函数
    "get_tools",
]

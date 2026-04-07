"""工具系统 — 注册所有 provider 并暴露公共 API。"""

from __future__ import annotations

from langchain_core.tools.structured import StructuredTool

from lumi.utils.logger import logger

from .loader import AgentConfig, SkillConfig, load_agents, load_skills
from .providers import (
    ask,
    background_task,
    bash,
    cron,
    filesystem,
    mcp,
    plan,
    skill,
    todo,
)
from .registry import ToolRegistry

# ------------------------------------------------------------------
# Provider 注册
# ------------------------------------------------------------------

ToolRegistry.register("mcp", mcp.get_mcp_tools)
ToolRegistry.register("filesystem", filesystem)
ToolRegistry.register("bash", bash)
ToolRegistry.register("todo", todo)
ToolRegistry.register("ask", ask)
ToolRegistry.register("cron", cron)
ToolRegistry.register("skill", skill)
ToolRegistry.register("plan", plan)
ToolRegistry.register("background_task", background_task)

# 条件注册: 仅在有 agent 配置时才启用
try:
    _agents = load_agents()
    if _agents:
        from .providers import agent

        agent._init_schema(_agents)
        ToolRegistry.register("agent", agent)
except Exception as e:
    logger.warning(f"加载 agent 配置失败，'agent' 工具不可用: {e}")


# ------------------------------------------------------------------
# 公共 API
# ------------------------------------------------------------------


async def get_tools(
    tools: list[str] | None = None,
    disabled_tools: list[str] | None = None,
) -> list[StructuredTool]:
    """获取工具列表，支持白名单 + 黑名单过滤。

    Args:
        tools: 白名单 — 只保留这些工具。``None`` 表示全部。
        disabled_tools: 黑名单 — 从结果中移除（优先级高于白名单）。
    """
    result = await ToolRegistry.instance().get_tools()

    if tools:
        allowed = set(tools)
        result = [t for t in result if t.name in allowed]

    if disabled_tools:
        blocked = set(disabled_tools)
        result = [t for t in result if t.name not in blocked]

    return result


__all__ = [
    "AgentConfig",
    "SkillConfig",
    "ToolRegistry",
    "get_tools",
    "load_agents",
    "load_skills",
]

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
    present_files,
    skill,
    todo,
    workflow,
)
from .registry import ToolRegistry, get_tool_registry

# ------------------------------------------------------------------
# Provider 注册
# ------------------------------------------------------------------

_registry = get_tool_registry()
_registry.register("mcp", mcp.get_mcp_tools)
_registry.register("filesystem", filesystem)
_registry.register("bash", bash)
_registry.register("todo", todo)
_registry.register("ask", ask)
_registry.register("cron", cron)
_registry.register("skill", skill)
_registry.register("background_task", background_task)
_registry.register("workflow", workflow)
_registry.register("present_files", present_files)

# 条件注册: 仅在有 agent 配置时才启用
try:
    _agents = load_agents()
    if _agents:
        from .providers import agent

        agent._init_schema(_agents)
        _registry.register("agent", agent)
except (FileNotFoundError, ValueError, OSError) as e:
    logger.warning("加载 agent 配置失败，'agent' 工具不可用: %s", e)
except Exception:
    logger.error("agent 工具初始化出现意外错误", exc_info=True)


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
    result = await get_tool_registry().get_tools()

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
    "get_tool_registry",
    "get_tools",
    "load_agents",
    "load_skills",
]

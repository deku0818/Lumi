"""工具系统 — 注册所有 provider 并暴露公共 API。"""

from __future__ import annotations

from langchain_core.tools.structured import StructuredTool

from .loader import AgentConfig, SkillConfig, load_agents, load_skills
from .providers import (
    agent,
    ask,
    background_task,
    bash,
    cron,
    filesystem,
    mcp,
    present_files,
    skill,
    todo,
    vision,
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
# vision 工具条件注册：仅当配置了视觉辅助模型（config.json 的 vision 段）时才出现，
# 供无视觉主模型带具体问题识别图片/PDF（本地路径或 http(s) URL）。
_registry.register("vision", vision.get_vision_tools)
# agent 工具静态注册：可用代理列表经 <system-reminder> 动态注入（见 AgentChangeDetector），
# 与 skill 一致——新增/删除 .lumi/agents 下的代理无需重建工具 schema。
_registry.register("agent", agent)


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

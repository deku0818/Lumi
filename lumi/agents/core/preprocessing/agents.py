"""Agent 列表的 ``<system-reminder>`` 块格式化。

将可用 agent 列表格式化为 ``<system-reminder>`` 块（供 agent 工具调用），由
:mod:`turn_context` 组进每轮 prepend 的上下文消息。``<system-reminder>`` 包装
逻辑与技能列表共用 [[messages]] 的 format_reminder。
"""

from __future__ import annotations

from lumi.agents.core.node_helpers.messages import format_reminder
from lumi.agents.tools.loader import AgentConfig

_AGENT_HEADER = "以下 agent 可用于 agent 工具:"


def format_agent_reminder(agents: list[AgentConfig]) -> str:
    """将 agent 列表格式化为 ``<system-reminder>`` 块。"""
    return format_reminder(
        _AGENT_HEADER, [f"- {a.name}: {a.description}" for a in agents]
    )

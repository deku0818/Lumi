"""Agent 注入模块

将可用 agent 列表格式化为 ``<system-reminder>`` 块并注入到用户消息中，
使 LLM 始终感知最新的可用子代理列表（供 agent 工具调用）。``<system-reminder>``
包装逻辑与技能列表注入共用 [[messages]] 的 format_reminder。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import (
    format_reminder,
    inject_text_into_message,
)
from lumi.agents.tools.loader import AgentConfig

_AGENT_HEADER = "以下 agent 可用于 agent 工具:"


def format_agent_reminder(agents: list[AgentConfig]) -> str:
    """将 agent 列表格式化为 ``<system-reminder>`` 块。"""
    return format_reminder(
        _AGENT_HEADER, [f"- {a.name}: {a.description}" for a in agents]
    )


def inject_agents_into_message(
    message: HumanMessage,
    agents: list[AgentConfig],
) -> HumanMessage:
    """将 agent ``<system-reminder>`` 块注入到用户消息 content 最前面，返回新消息。"""
    return inject_text_into_message(message, format_agent_reminder(agents))

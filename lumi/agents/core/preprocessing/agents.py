"""Agent 列表的条目行格式化。

输出 ``{name: 行文本}``，由 :mod:`context_inject` 组装为全量块或条目级 diff。
``<system-reminder>`` 包装逻辑与技能列表共用 [[messages]] 的 format_reminder。
"""

from __future__ import annotations

from lumi.agents.tools.loader import AgentConfig

AGENT_HEADER = "以下 agent 可用于 agent 工具:"


def agent_lines(agents: list[AgentConfig]) -> dict[str, str]:
    """agent 列表 → ``{name: 条目行}``（按名排序，确定性）。"""
    return {
        a.name: f"- {a.name}: {a.description}"
        for a in sorted(agents, key=lambda a: a.name)
    }

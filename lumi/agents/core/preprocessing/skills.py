"""技能列表的 ``<system-reminder>`` 块格式化。

将技能列表格式化为 ``<system-reminder>`` 块（供 skill 工具调用），由
:mod:`turn_context` 组进每轮 prepend 的上下文消息。``<system-reminder>`` 包装
逻辑与 agent 列表共用 [[messages]] 的 format_reminder。
"""

from __future__ import annotations

from lumi.agents.core.node_helpers.messages import format_reminder
from lumi.agents.tools.loader import SkillConfig

_SKILL_HEADER = "以下技能可用于 skill 工具:"


def _format_skill_line(skill: SkillConfig) -> str:
    """将单个技能格式化为列表行。"""
    trigger: str | None = getattr(skill, "trigger", None)
    if trigger:
        return f"- {skill.name}: {skill.description}（触发条件：{trigger}）"
    return f"- {skill.name}: {skill.description}"


def format_skill_reminder(skills: list[SkillConfig]) -> str:
    """将技能列表格式化为 ``<system-reminder>`` 块。"""
    return format_reminder(_SKILL_HEADER, [_format_skill_line(s) for s in skills])

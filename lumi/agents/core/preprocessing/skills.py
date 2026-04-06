"""技能注入模块

将技能列表格式化为 ``<system-reminder>`` 块并注入到用户消息中，
使 LLM 始终感知最新的可用技能列表。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import inject_text_into_message
from lumi.agents.tools.loader import SkillConfig


def _format_skill_line(skill: SkillConfig) -> str:
    """将单个技能格式化为列表行。"""
    trigger: str | None = getattr(skill, "trigger", None)
    if trigger:
        return f"- {skill.name}: {skill.description}（触发条件：{trigger}）"
    return f"- {skill.name}: {skill.description}"


def format_skill_reminder(skills: list[SkillConfig]) -> str:
    """将技能列表格式化为 ``<system-reminder>`` 块。"""
    skill_list = "\n".join(_format_skill_line(s) for s in skills)
    return f"<system-reminder>\n以下技能可用于 skill 工具:\n{skill_list}\n</system-reminder>\n"


def inject_skills_into_message(
    message: HumanMessage,
    skills: list[SkillConfig],
) -> HumanMessage:
    """将技能 ``<system-reminder>`` 块注入到用户消息 content 最前面，返回新消息。"""
    return inject_text_into_message(message, format_skill_reminder(skills))

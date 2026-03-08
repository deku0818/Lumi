"""技能注入模块

将技能列表格式化为 <system-reminder> 块并注入到用户消息中，
使 LLM 始终能感知到最新的可用技能列表。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.message_tools import inject_text_into_message
from lumi.agents.tools.config import SkillConfig


def format_skill_reminder(skills: list[SkillConfig]) -> str:
    """将技能列表格式化为 <system-reminder> 块。

    Args:
        skills: 技能配置列表

    Returns:
        格式化后的 system-reminder 文本
    """
    lines: list[str] = []
    for skill in skills:
        trigger = getattr(skill, "trigger", None)
        if trigger:
            lines.append(f"- {skill.name}: {skill.description}（触发条件：{trigger}）")
        else:
            lines.append(f"- {skill.name}: {skill.description}")

    skill_list = "\n".join(lines)
    return f"<system-reminder>\n以下技能可用于 skill 工具\n{skill_list}\n</system-reminder>\n"


def inject_skills_into_message(
    message: HumanMessage,
    skills: list[SkillConfig],
) -> HumanMessage:
    """将技能 system-reminder 块注入到用户消息中。

    system-reminder 块插入到用户原始内容之前，确保 LLM 先感知技能列表。
    返回新的 HumanMessage 对象，不修改原消息（不可变原则）。
    当 content 为字符串时，先转换为列表格式再插入。

    Args:
        message: 原始用户消息
        skills: 技能配置列表

    Returns:
        注入后的新 HumanMessage
    """
    reminder_text = format_skill_reminder(skills)
    return inject_text_into_message(message, reminder_text)

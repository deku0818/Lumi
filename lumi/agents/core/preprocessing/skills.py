"""技能列表的条目行格式化。

输出 ``{name: 行文本}``，由 :mod:`context_inject` 组装为全量块或条目级 diff。
``<system-reminder>`` 包装逻辑与 agent 列表共用 [[messages]] 的 format_reminder。
"""

from __future__ import annotations

from lumi.agents.tools.loader import SkillConfig

SKILL_HEADER = "以下技能可用于 skill 工具:"


def _format_skill_line(skill: SkillConfig) -> str:
    """将单个技能格式化为列表行。"""
    trigger: str | None = getattr(skill, "trigger", None)
    if trigger:
        return f"- {skill.name}: {skill.description}（触发条件：{trigger}）"
    return f"- {skill.name}: {skill.description}"


def skill_lines(skills: list[SkillConfig]) -> dict[str, str]:
    """技能列表 → ``{name: 条目行}``（按名排序，确定性）。"""
    return {s.name: _format_skill_line(s) for s in sorted(skills, key=lambda s: s.name)}

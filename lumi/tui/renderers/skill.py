"""技能工具（skill）渲染器

标题格式: skill(技能名称)
参数区域: 无（name 已在标题中展示）
输出区域: 以折叠形式展示技能返回的提示词内容，长文本截断
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.theme import get_color

# 提示词内容截断阈值（字符数）
_PROMPT_MAX_LEN = 500


class SkillRenderer:
    """skill 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: skill(技能名称)"""
        skill_name = args.get("name", "unknown")
        if not skill_name:
            skill_name = "unknown"
        return f"skill({skill_name})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """skill 参数仅 name，已在标题中展示，无需额外渲染"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """以折叠形式展示技能返回的提示词内容

        超过 500 字符时截断并添加省略号提示。
        """
        if not output:
            return Static("", markup=False)

        result = Text()
        result.append("📜 提示词内容:\n", style=f"bold {get_color('accent')}")

        if len(output) > _PROMPT_MAX_LEN:
            result.append(output[:_PROMPT_MAX_LEN], style=get_color("text_muted"))
            result.append(
                f"\n... (共 {len(output)} 字符)",
                style=f"italic {get_color('text_muted')}",
            )
        else:
            result.append(output, style=get_color("text_muted"))

        return Static(result)

"""技能工具（skill）渲染器

标题格式: skill(技能名称)
摘要格式: Loaded prompt (N chars)
参数区域: 无（name 已在标题中展示）
输出区域: 以折叠形式展示技能返回的提示词内容，长文本截断
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.theme import get_color

# 提示词内容截断阈值（字符数）
_PROMPT_MAX_LEN = 500


@register_renderer("skill")
class SkillRenderer(BaseRenderer):
    """skill 工具渲染器"""

    title_arg_key = "name"

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Loaded prompt (N chars)"""
        if is_error:
            return "Error"
        if not output:
            return "Done"
        return f"Loaded prompt ({len(output)} chars)"

    def render_output(self, output: str) -> Widget:
        """以折叠形式展示技能返回的提示词内容，超过 500 字符时截断。"""
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

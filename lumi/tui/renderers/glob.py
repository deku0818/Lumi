"""文件搜索工具（glob）渲染器

标题格式: glob(搜索模式)
摘要格式: Matched N files
参数区域: 无（模式已在标题中展示）
输出区域: 以列表形式展示匹配到的文件路径
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.theme import get_color


@register_renderer("glob")
class GlobRenderer(BaseRenderer):
    """glob 工具渲染器"""

    title_arg_key = "pattern"
    group_verb = "Searched"
    group_verb_active = "Searching"
    group_noun = "pattern"

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Matched N files"""
        if is_error:
            return "Error"
        if not output:
            return "No matches"
        paths = [line.strip() for line in output.splitlines() if line.strip()]
        return f"Matched {len(paths)} files"

    def render_output(self, output: str) -> Widget:
        """以列表形式展示匹配到的文件路径。"""
        if not output:
            return Static("", markup=False)

        paths = [line.strip() for line in output.splitlines() if line.strip()]

        result = Text()
        for path in paths:
            result.append("  📄 ", style=get_color("info"))
            result.append(path + "\n", style=get_color("text_muted"))

        return Static(result)

"""文件搜索工具（glob）渲染器

标题格式: glob(搜索模式)
参数区域: 无（模式已在标题中展示）
输出区域: 以列表形式展示匹配到的文件路径，并显示匹配文件总数
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.theme import get_color


class GlobRenderer(BaseRenderer):
    """glob 工具渲染器"""

    title_arg_key = "pattern"

    def render_output(self, output: str) -> Widget:
        """以列表形式展示匹配到的文件路径，并显示匹配文件总数。"""
        if not output:
            return Static("", markup=False)

        paths = [line.strip() for line in output.splitlines() if line.strip()]
        total = len(paths)

        result = Text()
        result.append(f"🔍 匹配 {total} 个文件\n", style=f"bold {get_color('accent')}")
        for path in paths:
            result.append("  📄 ", style=get_color("info"))
            result.append(path + "\n", style=get_color("text_muted"))

        return Static(result)

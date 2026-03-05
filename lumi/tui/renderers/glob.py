"""文件搜索工具（glob）渲染器

标题格式: glob(搜索模式)
参数区域: 无（模式已在标题中展示）
输出区域: 以列表形式展示匹配到的文件路径，并显示匹配文件总数
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color


class GlobRenderer:
    """glob 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: glob(搜索模式)"""
        return f"glob({get_arg(args, 'pattern')})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """glob 参数简单（pattern + path），模式已在标题中展示，无需额外渲染"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """以列表形式展示匹配到的文件路径，并显示匹配文件总数

        解析 glob 工具输出，每行一个文件路径，顶部显示匹配总数。
        """
        if not output:
            return Static("", markup=False)

        # 解析文件路径列表（每行一个路径）
        paths = [line.strip() for line in output.splitlines() if line.strip()]
        total = len(paths)

        result = Text()
        # 显示匹配总数
        result.append(f"🔍 匹配 {total} 个文件\n", style=f"bold {get_color('accent')}")

        # 逐行展示文件路径
        for path in paths:
            result.append("  📄 ", style=get_color("info"))
            result.append(path + "\n", style=get_color("text_muted"))

        return Static(result)

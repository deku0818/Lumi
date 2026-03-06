"""文件读取工具（read）渲染器

标题格式: read(文件路径)
参数区域: 无（参数简单，路径已在标题中展示）
输出区域: 带行号的语法高亮代码块展示读取到的文件内容，超过 50 行时显示行数提示
"""

from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg, guess_lexer
from lumi.tui.theme import get_color

# 折叠摘要的行数阈值
_LINE_THRESHOLD = 50


class ReadRenderer:
    """read 工具渲染器"""

    def __init__(self) -> None:
        self._path: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: read(文件路径)"""
        self._path = get_arg(args, "file_path")
        return f"read({self._path})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """read 参数简单（path/offset/limit），路径已在标题中展示，无需额外渲染"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """以带行号的语法高亮代码块展示读取到的文件内容

        超过 50 行时显示行数提示。
        """
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        line_count = len(lines)

        # 超过阈值时显示行数提示
        if line_count > _LINE_THRESHOLD:
            summary = Text(
                f"📄 {line_count} 行内容",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        # 根据文件扩展名推断语言
        lexer = guess_lexer(self._path)
        syntax = Syntax(
            output,
            lexer,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        return Static(syntax)

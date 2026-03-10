"""文件写入工具（write）渲染器

标题格式: write(文件路径)
参数区域: 语法高亮代码块展示文件内容，超过 50 行时显示行数提示
输出区域: 写入成功/失败状态
"""

from __future__ import annotations

from rich.syntax import Syntax
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import guess_lexer, render_status_output
from lumi.tui.theme import get_color

# 折叠摘要的行数阈值
_LINE_THRESHOLD = 50


class WriteRenderer(BaseRenderer):
    """write 工具渲染器"""

    title_arg_key = "file_path"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以语法高亮代码块展示将要写入的文件内容。

        超过 50 行时显示行数提示（审批模式下展示完整内容）。
        """
        content = args.get("content", "")
        path = args.get("file_path", "")

        if not content:
            return Static("", markup=False)

        line_count = content.count("\n") + (1 if not content.endswith("\n") else 0)

        if not approval_mode and line_count > _LINE_THRESHOLD:
            summary = Text(
                f"📄 {line_count} 行内容",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        lexer = guess_lexer(path)
        syntax = Syntax(
            content,
            lexer,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        return Static(syntax)

    def render_output(self, output: str) -> Widget:
        """显示写入成功/失败状态"""
        return render_status_output(output)

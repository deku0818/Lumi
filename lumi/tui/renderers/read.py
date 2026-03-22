"""文件读取工具（read）渲染器

标题格式: read(文件路径)
参数区域: 无（参数简单，路径已在标题中展示）
输出区域: 带行号的语法高亮代码块展示读取到的文件内容，超过 50 行时显示行数提示
"""

from __future__ import annotations

from rich.syntax import Syntax
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import guess_lexer, make_summary_static

# 折叠摘要的行数阈值
_LINE_THRESHOLD = 50


@register_renderer("read")
class ReadRenderer(BaseRenderer):
    """read 工具渲染器"""

    title_arg_key = "file_path"
    group_verb = "Read"
    group_verb_active = "Reading"
    group_noun = "file"

    def __init__(self) -> None:
        self._path: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: read(文件路径)，同时缓存路径供 render_output 使用。"""
        self._path = args.get("file_path", "unknown")
        return super().render_title(name, args)

    def render_output(self, output: str) -> Widget:
        """以带行号的语法高亮代码块展示读取到的文件内容，超过 50 行时显示行数提示。"""
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        line_count = len(lines)

        if line_count > _LINE_THRESHOLD:
            return make_summary_static(f"📄 {line_count} 行内容")

        lexer = guess_lexer(self._path)
        syntax = Syntax(
            output,
            lexer,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        return Static(syntax)

"""文件读取工具（read）渲染器

标题格式: read(文件路径)
参数区域: 无（参数简单，路径已在标题中展示）
输出区域: 带行号的语法高亮代码块展示读取到的文件内容，超过 50 行时显示行数提示
"""

from __future__ import annotations

import os

from rich.syntax import Syntax
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.theme import get_color

# 折叠摘要的行数阈值
_LINE_THRESHOLD = 50


class ReadRenderer:
    """read 工具渲染器"""

    def __init__(self) -> None:
        self._path: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: read(文件路径)"""
        path = args.get("path", "unknown")
        if not path:
            path = "unknown"
        self._path = path
        return f"read({path})"

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
        lexer = _guess_lexer(self._path)
        syntax = Syntax(
            output,
            lexer,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        return Static(syntax)


def _guess_lexer(path: str) -> str:
    """根据文件路径推断语法高亮语言"""
    if not path:
        return "text"

    _, ext = os.path.splitext(path)
    ext = ext.lower()

    lexer_map: dict[str, str] = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".sh": "bash",
        ".bash": "bash",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".rb": "ruby",
        ".sql": "sql",
        ".xml": "xml",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
    }

    return lexer_map.get(ext, "text")

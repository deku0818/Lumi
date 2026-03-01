"""文件写入工具（write）渲染器

标题格式: write(文件路径)
参数区域: 语法高亮代码块展示文件内容，超过 50 行时显示行数提示
输出区域: 写入成功/失败状态
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


class WriteRenderer:
    """write 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: write(文件路径)"""
        path = args.get("path", "unknown")
        if not path:
            path = "unknown"
        return f"write({path})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以语法高亮代码块展示将要写入的文件内容

        超过 50 行时在折叠摘要中显示行数提示（审批模式下跳过折叠）。
        """
        content = args.get("content", "")
        path = args.get("path", "")

        if not content:
            return Static("", markup=False)

        line_count = content.count("\n") + (1 if not content.endswith("\n") else 0)

        # 超过阈值时显示行数提示（审批模式下展示完整内容）
        if not approval_mode and line_count > _LINE_THRESHOLD:
            summary = Text(
                f"📄 {line_count} 行内容",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        # 根据文件扩展名推断语言
        lexer = _guess_lexer(path)
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
        if not output:
            return Static("", markup=False)

        lower = output.lower()
        if "error" in lower or "fail" in lower or "traceback" in lower:
            return Static(Text(output, style=get_color("error")))

        return Static(Text(output, style=get_color("success")))


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

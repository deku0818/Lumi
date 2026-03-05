"""渲染器共享工具函数"""

from __future__ import annotations

import os

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.theme import get_color

# spinner 动画帧序列，供 ToolBlock 和 ThinkingIndicator 共用
SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# 文件扩展名 → 语法高亮语言映射
_LEXER_MAP: dict[str, str] = {
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


def guess_lexer(path: str) -> str:
    """根据文件路径推断语法高亮语言。

    Args:
        path: 文件路径

    Returns:
        Pygments lexer 名称，无法推断时返回 ``"text"``。
    """
    if not path:
        return "text"
    _, ext = os.path.splitext(path)
    return _LEXER_MAP.get(ext.lower(), "text")


def get_arg(args: dict, key: str, fallback: str = "unknown") -> str:
    """从参数字典中取值，空值回退到 fallback。

    Args:
        args: 工具参数字典
        key: 参数键名
        fallback: 值为空时的回退值

    Returns:
        参数值字符串
    """
    value = args.get(key, fallback)
    return value if value else fallback


def render_status_output(output: str) -> Widget:
    """渲染成功/失败状态输出，适用于 write、edit 等工具。

    检测输出中是否包含错误关键词，成功时绿色，失败时红色。

    Args:
        output: 工具输出文本

    Returns:
        带颜色的 Static Widget
    """
    if not output:
        return Static("", markup=False)
    lower = output.lower()
    if "error" in lower or "fail" in lower or "traceback" in lower:
        return Static(Text(output, style=get_color("error")))
    return Static(Text(output, style=get_color("success")))

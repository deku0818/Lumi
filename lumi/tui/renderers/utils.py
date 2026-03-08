"""渲染器共享工具函数"""

from __future__ import annotations

import os

from rich.text import Text
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.theme import get_color

# spinner 动画帧序列，供 ToolBlock 和 ThinkingIndicator 共用
SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class SpinnerMixin:
    """Spinner 动画 mixin，消除 ThinkingIndicator 和 ToolBlock 的重复代码。

    宿主类需要是 Textual Widget（提供 ``set_interval``）。
    子类须实现 ``_on_spinner_tick(frame_char)`` 来响应每一帧。
    """

    _spinner_frame: int = 0
    _spinner_timer: Timer | None = None

    def _start_spinner(self, interval: float = 0.1) -> None:
        self._spinner_frame = 0
        self._spinner_timer = self.set_interval(interval, self.__spinner_tick)  # type: ignore[attr-defined]

    def __spinner_tick(self) -> None:
        char = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
        self._spinner_frame += 1
        self._on_spinner_tick(char)

    def _on_spinner_tick(self, frame_char: str) -> None:
        raise NotImplementedError

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None


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


# 标题中参数值的最大显示长度
_TITLE_MAX_LEN = 60


def truncate_for_title(text: str, max_len: int = _TITLE_MAX_LEN) -> str:
    """截断文本用于标题显示，只保留第一行且限制长度。

    多行文本只取第一行，超出 max_len 时截断并附带隐藏字符数提示。

    Args:
        text: 原始文本
        max_len: 最大显示长度

    Returns:
        截断后的单行文本
    """
    first_line = text.split("\n", 1)[0].strip()
    total = len(text.strip())
    if len(first_line) <= max_len and total == len(first_line):
        return first_line
    shown = min(len(first_line), max_len)
    hidden = total - shown
    return f"{first_line[:max_len]} ...[+{hidden} chars]"


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

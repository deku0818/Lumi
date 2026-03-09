"""命令结果覆盖面板 - 用于展示 BUILTIN 命令（如 /skills）的输出。

面板覆盖在 ChatLog 底部，按 Esc 关闭后在 ChatLog 中留一行状态提示。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.events import Key
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


class CommandResultPanel(Widget):
    """底部覆盖面板，展示 BUILTIN 命令的执行结果。"""

    DEFAULT_CSS = """
    CommandResultPanel {
        layer: overlay;
        dock: bottom;
        display: none;
        height: auto;
        max-height: 60%;
        background: $surface;
        border-top: solid $accent;
    }

    #cmd-result-content {
        height: auto;
        max-height: 100%;
        padding: 1 2;
        scrollbar-color: $scrollbar;
        scrollbar-color-hover: $accent;
    }

    #cmd-result-hint {
        color: $text-muted;
        height: 1;
        text-style: italic;
        padding: 0 2;
    }
    """

    class Dismissed(Message):
        """面板被关闭时发出的消息。"""

        def __init__(self, command_name: str) -> None:
            super().__init__()
            self.command_name = command_name

    def __init__(self) -> None:
        super().__init__()
        self._command_name: str = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="cmd-result-content"):
            yield Static(id="cmd-result-body", markup=False)
        yield Static("Esc to close", id="cmd-result-hint")

    @property
    def is_visible(self) -> bool:
        """面板当前是否可见。"""
        return self.styles.display != "none"

    def show(self, content: Text | str, command_name: str = "") -> None:
        """显示面板并更新内容。"""
        self._command_name = command_name
        self.query_one("#cmd-result-body", Static).update(content)
        self.styles.display = "block"
        self.focus()

    def hide(self) -> str:
        """隐藏面板，返回命令名。"""
        self.styles.display = "none"
        name = self._command_name
        self._command_name = ""
        return name

    def on_key(self, event: Key) -> None:
        if event.key == "escape" and self.is_visible:
            name = self.hide()
            self.post_message(self.Dismissed(name))
            event.stop()

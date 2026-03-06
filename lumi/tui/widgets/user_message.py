"""用户消息组件"""

from rich.text import Text
from textual.widgets import Static

from lumi.tui.theme import get_color


class UserMessage(Static):
    """用户消息 - 带 > 前缀"""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 0 1 0;
        padding: 0 1;
        width: auto;
        height: auto;
        color: $foreground;
        background: $surface;
    }
    """

    def __init__(self, text: str) -> None:
        display = Text()
        display.append("> ", style=f"bold {get_color('accent')}")
        display.append(text)
        super().__init__(display, classes="user-message", markup=False)

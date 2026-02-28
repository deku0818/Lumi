"""用户消息组件"""

from rich.text import Text
from textual.widgets import Static


class UserMessage(Static):
    """用户消息 - 带 > 前缀"""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 0 0 0;
        padding: 0 1;
        color: #e0e0e0;
        background: #1e1e2e;
        width: auto;
        height: auto;
    }
    """

    def __init__(self, text: str) -> None:
        display = Text()
        display.append("> ", style="bold #ffcc00")
        display.append(text)
        super().__init__(display, classes="user-message", markup=False)

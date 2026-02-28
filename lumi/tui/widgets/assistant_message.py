"""AI 助手消息组件 - 支持流式 token 追加"""

from rich.text import Text
from textual.widgets import Static


class AssistantMessage(Static):
    """AI 助手消息 - 带 ● 前缀，支持流式追加"""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 0 0 0 0;
        padding: 0 1;
        color: #e0e0e0;
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__("", classes="assistant-message", markup=False)
        self._text = Text()
        self._text.append("● ", style="bold #ffcc00")
        self._has_content = False

    def append_token(self, token: str) -> None:
        """追加流式 token"""
        self._text.append(token)
        self._has_content = True
        self.update(self._text)

    def finalize(self) -> None:
        """标记消息完成"""
        self.update(self._text)

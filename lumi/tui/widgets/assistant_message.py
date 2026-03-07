"""AI 助手消息组件 - 支持流式 token 追加"""

from rich.text import Text
from textual.widgets import Static

from lumi.tui.theme import get_color


class AssistantMessage(Static):
    """AI 助手消息 - 带 ● 前缀，支持流式追加"""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 0 1 0;
        padding: 0 1;
        height: auto;
        color: $foreground;
    }
    """

    def __init__(self) -> None:
        super().__init__("", classes="assistant-message", markup=False)
        self._text = Text()
        self._text.append("● ", style=f"bold {get_color('accent')}")
        self._has_content = False

    def append_token(self, token: str) -> None:
        """追加流式 token"""
        self._text.append(token)
        self._has_content = True
        self.update(self._text)

    def finalize(self) -> None:
        """标记消息完成，去除末尾多余空行

        使用 right_crop 按字符数移除末尾换行，避免 truncate 按 cell width
        截断导致中文内容（每字符占 2 cell）被多截。
        """
        plain = self._text.plain
        trailing = len(plain) - len(plain.rstrip("\n"))
        if trailing > 0:
            self._text.right_crop(trailing)
            self.update(self._text)

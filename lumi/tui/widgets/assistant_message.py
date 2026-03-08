"""AI 助手消息组件 - 支持流式 token 追加，Markdown 渲染"""

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Markdown, Static

from lumi.tui.theme import get_color


class AssistantMessage(Horizontal):
    """AI 助手消息 - 带 ● 前缀，内容 Markdown 渲染并与首行对齐"""

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 0 1 0;
        padding: 0 1;
        height: auto;
        color: $foreground;
        background: transparent;
    }
    AssistantMessage > .prefix {
        width: auto;
        min-width: 2;
        max-width: 2;
        height: auto;
        min-height: 1;
        padding: 0;
        margin: 0;
    }
    AssistantMessage > .body {
        width: 1fr;
        height: auto;
        background: transparent;
        margin: 0;
        padding: 0;
    }
    AssistantMessage > .body > * {
        margin: 0 0 1 0;
    }
    AssistantMessage > .body > *:last-child {
        margin: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(classes="assistant-message")
        prefix = Text("● ", style=f"bold {get_color('accent')}")
        self._prefix = Static(prefix, classes="prefix", markup=False)
        self._body = Markdown("", classes="body")
        self._raw = ""
        self._has_content = False

    def compose(self):
        """组合圆点前缀和 Markdown 主体"""
        yield self._prefix
        yield self._body

    def append_token(self, token: str) -> None:
        """追加流式 token，重新渲染 Markdown"""
        self._raw += token
        self._has_content = True
        self._body.update(self._raw)

    def finalize(self) -> None:
        """标记消息完成，去除末尾多余空行后做最终渲染"""
        self._raw = self._raw.rstrip("\n")
        self._body.update(self._raw)

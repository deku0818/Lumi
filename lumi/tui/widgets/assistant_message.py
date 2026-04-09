"""AI 助手消息组件 - 支持流式 token 追加，Markdown 实时渲染

流式阶段即使用 Markdown widget 渲染，节流间隔 300ms 控制开销。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Markdown, Static

from lumi.tui.theme import get_color


class AssistantMessage(Horizontal):
    """AI 助手消息 - 带 ● 前缀，内容 Markdown 实时渲染

    流式阶段直接使用 Markdown widget，节流间隔 300ms 合并多次 token。
    """

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

    # 节流间隔（秒）— 300ms 平衡 Markdown 渲染开销与流畅度
    _THROTTLE_INTERVAL: float = 0.3

    def __init__(self) -> None:
        super().__init__(classes="assistant-message")
        prefix = Text("● ", style=f"bold {get_color('accent')}")
        self._prefix: Static = Static(prefix, classes="prefix", markup=False)
        self._body: Markdown = Markdown("", classes="body")
        self._raw: str = ""
        self._has_content: bool = False
        self._dirty: bool = False
        self._update_scheduled: bool = False
        self._finalized: bool = False

    def compose(self) -> ComposeResult:
        yield self._prefix
        yield self._body

    def append_token(self, token: str) -> None:
        """追加流式 token，节流后批量更新 Markdown。"""
        self._raw += token
        self._has_content = True
        self._dirty = True
        if not self._update_scheduled:
            self._update_scheduled = True
            self.set_timer(self._THROTTLE_INTERVAL, self._flush_update)

    def _flush_update(self) -> None:
        """定时器回调：将累积的 token 一次性更新到 Markdown。"""
        self._update_scheduled = False
        if self._dirty:
            self._dirty = False
            self._body.update(self._raw)

    def finalize(self) -> None:
        """标记消息完成，做最终渲染。"""
        if self._finalized:
            return
        self._finalized = True
        self._raw = self._raw.rstrip("\n")
        self._dirty = False
        self._update_scheduled = False
        self._body.update(self._raw)

    @property
    def is_finalized(self) -> bool:
        """消息是否已完成。"""
        return self._finalized

    def unfinalize(self) -> None:
        """重新开放已完成的消息以接收更多 token。"""
        self._finalized = False

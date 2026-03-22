"""AI 助手消息组件 - 支持流式 token 追加，Markdown 渲染

流式阶段使用轻量 Static 显示纯文本（零 Markdown 解析开销），
finalize 时切换为 Markdown 渲染最终内容，大幅降低流式输出时的 CPU 占用。
"""

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Markdown, Static

from lumi.tui.theme import get_color


class AssistantMessage(Horizontal):
    """AI 助手消息 - 带 ● 前缀，内容 Markdown 渲染并与首行对齐

    流式阶段用 Static 显示纯文本，finalize 后替换为 Markdown 渲染。
    节流间隔 150ms，合并多次 token 为一次 Static.update()。
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

    # 节流间隔（秒）— 150ms 平衡流畅度与渲染开销
    _THROTTLE_INTERVAL: float = 0.15

    def __init__(self) -> None:
        super().__init__(classes="assistant-message")
        prefix = Text("● ", style=f"bold {get_color('accent')}")
        self._prefix = Static(prefix, classes="prefix", markup=False)
        # 流式阶段用 Static（纯文本更新，无 Markdown 解析开销）
        self._body_static = Static("", classes="body", markup=False)
        self._raw = ""
        self._has_content = False
        self._dirty = False
        self._update_scheduled = False
        self._finalized = False

    def compose(self):
        """组合圆点前缀和文本主体"""
        yield self._prefix
        yield self._body_static

    def append_token(self, token: str) -> None:
        """追加流式 token，节流后批量更新 Static。

        每次追加仅标记 dirty，通过定时器合并多次 token 为一次渲染。
        """
        self._raw += token
        self._has_content = True
        self._dirty = True
        if not self._update_scheduled:
            self._update_scheduled = True
            self.set_timer(self._THROTTLE_INTERVAL, self._flush_update)

    def _flush_update(self) -> None:
        """定时器回调：将累积的 token 一次性更新到 Static。"""
        self._update_scheduled = False
        if self._dirty:
            self._dirty = False
            self._body_static.update(self._raw)

    def finalize(self) -> None:
        """标记消息完成，替换为 Markdown 渲染最终内容。"""
        if self._finalized:
            return
        self._finalized = True
        self._raw = self._raw.rstrip("\n")
        self._dirty = False
        self._update_scheduled = False
        # 将 Static 替换为 Markdown 渲染
        md = Markdown(self._raw, classes="body")
        self._body_static.remove()
        self.mount(md)
        self._body_static = None

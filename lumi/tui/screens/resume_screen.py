"""会话恢复选择界面

展示历史会话列表，用户可通过 ↑↓ 选择、Enter 确认恢复、Esc 取消。
支持搜索过滤。
"""

from __future__ import annotations

from rich.text import Text

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Rule, Static

from lumi.tui.session_store import SessionSummary


class _SessionItem(Static):
    """单个会话条目"""

    DEFAULT_CSS = """
    _SessionItem {
        width: 100%;
        height: auto;
        padding: 0 2;
        color: $foreground;
    }
    _SessionItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, summary: SessionSummary, index: int) -> None:
        self._summary = summary
        self._index = index

        msg = summary.first_message.replace("\n", " ").strip()
        if len(msg) > 80:
            msg = msg[:77] + "..."

        text = Text()
        text.append(f"› {msg}\n", style="bold")
        text.append(
            f"  {summary.display_time} · {summary.message_count} messages",
            style="dim",
        )
        super().__init__(text, markup=False)

    @property
    def summary(self) -> SessionSummary:
        return self._summary

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态"""
        self.set_class(selected, "selected")


class ResumeScreen(ModalScreen[str | None]):
    """会话恢复选择界面

    显示历史会话列表，用户选择后返回 thread_id，取消返回 None。
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("ctrl+c", "quit_app", "Quit"),
    ]

    DEFAULT_CSS = """
    ResumeScreen {
        align: center middle;
    }

    ResumeScreen > Vertical {
        width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }

    ResumeScreen .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        width: 100%;
    }

    ResumeScreen #resume-search {
        margin-bottom: 1;
    }

    ResumeScreen #session-list {
        height: auto;
        max-height: 60vh;
    }

    ResumeScreen .hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }

    ResumeScreen .empty-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
        padding: 2 0;
    }
    """

    def __init__(self, sessions: list[SessionSummary]) -> None:
        """初始化会话恢复界面。

        Args:
            sessions: 可恢复的会话摘要列表（已按时间降序排列）
        """
        super().__init__()
        self._sessions = sessions
        self._filtered: list[SessionSummary] = list(sessions)
        self._selected_index: int = 0
        self._items: list[_SessionItem] = []

    def compose(self) -> ComposeResult:
        """渲染界面。"""
        container = Vertical()
        container.border_title = "Resume Session"
        with container:
            yield Label(
                f"Resume Session ({len(self._sessions)} of {len(self._sessions)})",
                classes="title",
                id="resume-title",
            )
            yield Input(placeholder="Search...", id="resume-search")
            yield Rule()
            yield VerticalScroll(id="session-list")
            yield Rule()
            yield Static(
                "↑↓ select · Enter resume · Esc cancel",
                classes="hint",
            )

    async def on_mount(self) -> None:
        """挂载后渲染会话列表。"""
        await self._render_list()
        self.query_one("#resume-search", Input).focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """搜索框内容变化时过滤列表。"""
        query = event.value.strip().lower()
        if query:
            self._filtered = [
                s
                for s in self._sessions
                if query in s.first_message.lower() or query in s.thread_id.lower()
            ]
        else:
            self._filtered = list(self._sessions)
        self._selected_index = 0
        await self._render_list()
        # 更新标题计数
        title = self.query_one("#resume-title", Label)
        title.update(f"Resume Session ({len(self._filtered)} of {len(self._sessions)})")

    async def _render_list(self) -> None:
        """重新渲染会话列表。"""
        container = self.query_one("#session-list", VerticalScroll)
        await container.remove_children()
        self._items.clear()

        if not self._filtered:
            await container.mount(Static("No sessions found", classes="empty-hint"))
            return

        for i, session in enumerate(self._filtered):
            item = _SessionItem(session, i)
            self._items.append(item)
            await container.mount(item)

        self._update_selection()

    def _update_selection(self) -> None:
        """更新选中状态高亮。"""
        for item in self._items:
            item.set_selected(item.index == self._selected_index)
        # 滚动到选中项
        if self._items and 0 <= self._selected_index < len(self._items):
            self._items[self._selected_index].scroll_visible()

    def _on_key(self, event: Key) -> None:
        """处理键盘事件（在 binding 之前触发，确保 escape 不被父级拦截）。"""
        match event.key:
            case "escape":
                self.dismiss(None)
                event.prevent_default()
                event.stop()
            case "up":
                if self._filtered:
                    self._selected_index = max(0, self._selected_index - 1)
                    self._update_selection()
                event.prevent_default()
                event.stop()
            case "down":
                if self._filtered:
                    self._selected_index = min(
                        len(self._filtered) - 1, self._selected_index + 1
                    )
                    self._update_selection()
                event.prevent_default()
                event.stop()
            case "enter":
                if self._filtered and 0 <= self._selected_index < len(self._filtered):
                    selected = self._filtered[self._selected_index]
                    self.dismiss(selected.thread_id)
                event.prevent_default()
                event.stop()

    def action_cancel(self) -> None:
        """取消选择。"""
        self.dismiss(None)

    async def action_quit_app(self) -> None:
        """ctrl+c 退出程序。"""
        self.app.exit()

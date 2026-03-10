"""会话恢复选择界面

展示历史会话列表，用户可通过 ↑↓ 选择、Enter 确认恢复、Esc 取消。
支持搜索过滤。基于 ListScreen 基类实现。
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.screens.list_screen import ListScreen
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


class ResumeScreen(ListScreen[SessionSummary]):
    """会话恢复选择界面

    显示历史会话列表，用户选择后返回 thread_id，取消返回 None。
    """

    @property
    def screen_title(self) -> str:
        return "Resume Session"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter resume · Esc cancel"

    @property
    def empty_text(self) -> str:
        return "没有可恢复的历史会话"

    @property
    def no_match_text(self) -> str:
        return "No sessions found"

    def match_filter(self, item: SessionSummary, query: str) -> bool:
        return query in item.first_message.lower() or query in item.thread_id.lower()

    def make_item_widget(self, item: SessionSummary, index: int) -> Widget:
        return _SessionItem(item, index)

    def get_dismiss_value(self, item: SessionSummary) -> str:
        return item.thread_id

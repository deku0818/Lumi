"""定时任务通知界面

展示 cron 任务执行结果通知列表，支持搜索、查看详情、标记已读、删除。
基于 ListScreen 基类实现。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Rule, Static

from lumi.tui.screens.list_screen import ListScreen
from lumi.tui.widgets.notification_panel import NotificationRecord, NotificationStore
from lumi.utils.logger import logger


def _format_meta(r: NotificationRecord) -> str:
    """格式化通知的时间和耗时信息。"""
    parts: list[str] = []
    if r.started_at is not None:
        parts.append(r.started_at.strftime("%m-%d %H:%M"))
    if r.duration_ms is not None:
        if r.duration_ms >= 60_000:
            mins, secs = divmod(r.duration_ms // 1000, 60)
            parts.append(f"{mins}m{secs}s")
        elif r.duration_ms >= 1000:
            parts.append(f"{r.duration_ms / 1000:.1f}s")
        else:
            parts.append(f"{r.duration_ms}ms")
    return " · ".join(parts)


class _NotifyItem(Static):
    """单条通知条目

    第一行：指示符 + job_name + 时间/耗时（未读加粗高亮）
    第二行：summary 截断
    """

    DEFAULT_CSS = """
    _NotifyItem {
        width: 100%;
        height: 3;
        padding: 0 2;
        color: $foreground;
    }
    _NotifyItem.selected {
        background: $accent 30%;
    }
    _NotifyItem.unread {
        color: $accent;
    }
    """

    def __init__(self, record: NotificationRecord, index: int) -> None:
        self._record = record
        self._index = index

        summary = record.summary.replace("\n", " ").strip()
        if len(summary) > 65:
            summary = summary[:62] + "..."

        meta = _format_meta(record)
        indicator = "●" if not record.read else "○"

        name_style = "bold" if not record.read else ""
        text = Text()
        text.append(f"{indicator} {record.job_name}", style=name_style)
        if meta:
            text.append(f" · {meta}", style="dim")
        text.append(f"\n  {summary}", style="dim")
        super().__init__(text, markup=False)
        if not record.read:
            self.add_class("unread")

    @property
    def record(self) -> NotificationRecord:
        return self._record

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态"""
        self.set_class(selected, "selected")


class _NotifyDetailScreen(ModalScreen[None]):
    """通知详情弹窗，展示完整输出内容。"""

    DEFAULT_CSS = """
    _NotifyDetailScreen {
        align: center middle;
    }
    _NotifyDetailScreen > Vertical {
        width: 90;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }
    _NotifyDetailScreen .detail-meta {
        color: $text-muted;
        width: 100%;
    }
    _NotifyDetailScreen .detail-content {
        height: auto;
        max-height: 60vh;
        padding: 0 1;
    }
    _NotifyDetailScreen .detail-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def __init__(self, record: NotificationRecord) -> None:
        super().__init__()
        self._record = record

    def compose(self) -> ComposeResult:
        r = self._record
        meta = _format_meta(r)
        container = Vertical()
        container.border_title = r.job_name
        with container:
            if meta:
                yield Static(meta, classes="detail-meta")
            yield Rule()
            with VerticalScroll(classes="detail-content"):
                yield Static(r.content, markup=False)
            yield Rule()
            yield Static("Esc back", classes="detail-hint")

    def _on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()
            event.stop()


class CronNotifyScreen(ListScreen[NotificationRecord]):
    """定时任务通知界面

    显示所有通知，支持搜索、查看详情、标记已读、删除。
    关闭时 dismiss("changed") 表示有变更，None 表示无变更。
    """

    def __init__(
        self,
        records: list[NotificationRecord],
        store: NotificationStore | None = None,
    ) -> None:
        super().__init__(records)
        self._store = store or NotificationStore()
        self._has_changed: bool = False

    @property
    def screen_title(self) -> str:
        return "Cron Notifications"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter 查看 · r 已读 · R 全部已读 · Delete 删除 · Esc close"

    @property
    def empty_text(self) -> str:
        return "暂无通知"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的通知"

    def _format_title(self, filtered_count: int, total_count: int) -> str:
        """标题显示未读/已读计数。"""
        unread = sum(1 for r in self._all_items if not r.read)
        read = sum(1 for r in self._all_items if r.read)
        return f"{self.screen_title} (未读 {unread} / 已读 {read})"

    def match_filter(self, item: NotificationRecord, query: str) -> bool:
        return query in item.job_name.lower() or query in item.content.lower()

    def make_item_widget(self, item: NotificationRecord, index: int) -> Widget:
        return _NotifyItem(item, index)

    def get_dismiss_value(self, item: NotificationRecord) -> str:
        return item.id

    # ── 键盘交互扩展 ──

    def _on_key(self, event: Key) -> None:
        """扩展键盘处理：Enter 查看详情、r 标记已读、R 全部已读、Delete 删除。"""
        match event.key:
            case "enter":
                if self._filtered and 0 <= self._selected_index < len(self._filtered):
                    record = self._filtered[self._selected_index]
                    self._mark_read(record)
                    self.app.push_screen(_NotifyDetailScreen(record))
                event.prevent_default()
                event.stop()
            case "delete":
                self._delete_selected()
                event.prevent_default()
                event.stop()
            case "r":
                # 搜索框有焦点时不拦截字母键
                if not self._is_search_focused():
                    self._mark_selected_read()
                    event.prevent_default()
                    event.stop()
                    return
                super()._on_key(event)
            case "R":
                if not self._is_search_focused():
                    self._mark_all_read()
                    event.prevent_default()
                    event.stop()
                    return
                super()._on_key(event)
            case _:
                super()._on_key(event)

    def _is_search_focused(self) -> bool:
        """判断搜索框是否有焦点。"""
        from textual.css.query import NoMatches
        from textual.widgets import Input

        try:
            search = self.query_one("#ls-search", Input)
            return search.has_focus
        except NoMatches:
            return False

    def _mark_read(self, record: NotificationRecord) -> None:
        """标记单条通知为已读。"""
        if record.read:
            return
        record.read = True
        self._has_changed = True
        self._save()

    def _mark_selected_read(self) -> None:
        """标记选中通知为已读并刷新。"""
        if not self._filtered or self._selected_index >= len(self._filtered):
            return
        record = self._filtered[self._selected_index]
        self._mark_read(record)
        self._refresh_sync()

    def _mark_all_read(self) -> None:
        """全部标记已读并刷新。"""
        changed = False
        for r in self._all_items:
            if not r.read:
                r.read = True
                changed = True
        if changed:
            self._has_changed = True
            self._save()
            self._refresh_sync()

    def _delete_selected(self) -> None:
        """删除选中通知并刷新。"""
        if not self._filtered or self._selected_index >= len(self._filtered):
            return
        record = self._filtered[self._selected_index]
        self._all_items = [r for r in self._all_items if r.id != record.id]
        self._filtered = [r for r in self._filtered if r.id != record.id]
        self._has_changed = True

        if self._selected_index >= len(self._filtered):
            self._selected_index = max(0, len(self._filtered) - 1)

        self._save()
        self._refresh_sync()

    def _save(self) -> None:
        """持久化当前记录。"""
        try:
            self._store.save(self._all_items)
        except OSError:
            logger.warning("[CronNotifyScreen] 通知持久化失败", exc_info=True)
            self.app.notify("通知保存失败", severity="warning")

    def _refresh_sync(self) -> None:
        """刷新列表和标题（同步触发异步任务）。"""
        self.call_later(self._refresh_ui)

    async def _refresh_ui(self) -> None:
        """刷新列表和标题。"""
        await self._render_list()
        self._update_title()

    def action_cancel(self) -> None:
        """关闭界面，返回是否有变更。"""
        self.dismiss("changed" if self._has_changed else None)

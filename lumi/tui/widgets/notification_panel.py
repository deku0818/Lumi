"""通知面板 - 右侧滑出式侧边栏，展示定时任务执行结果历史。

铃铛指示器集成在 InputBar 中，通知列表以 dock: right 侧边栏形式展示，
通过 Ctrl+N 切换显示/隐藏。通知持久化到 ``~/.lumi/cron/notifications.json``。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from lumi.utils.logger import logger

# 持久化文件路径
_NOTIFICATIONS_FILE: Path = Path.home() / ".lumi" / "cron" / "notifications.json"
_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%f"


@dataclass
class NotificationRecord:
    """单条通知记录。"""

    id: str
    job_name: str
    content: str
    summary: str
    timestamp: datetime  # 推送时间
    started_at: datetime | None = None  # 任务开始执行时间
    duration_ms: int | None = None  # 执行耗时（毫秒）
    read: bool = False

    @classmethod
    def create(
        cls,
        job_name: str,
        content: str,
        *,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> NotificationRecord:
        summary = content[:120] + ("…" if len(content) > 120 else "")
        return cls(
            id=str(uuid.uuid4()),
            job_name=job_name,
            content=content,
            summary=summary,
            timestamp=datetime.now(UTC),
            started_at=started_at,
            duration_ms=duration_ms,
        )

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典。"""
        d = asdict(self)
        d["timestamp"] = self.timestamp.strftime(_ISO_FMT)
        d["started_at"] = (
            self.started_at.strftime(_ISO_FMT) if self.started_at else None
        )
        return d

    @classmethod
    def from_dict(cls, d: dict) -> NotificationRecord:
        """从字典反序列化。"""
        return cls(
            id=d["id"],
            job_name=d["job_name"],
            content=d["content"],
            summary=d["summary"],
            timestamp=datetime.strptime(d["timestamp"], _ISO_FMT),
            started_at=datetime.strptime(d["started_at"], _ISO_FMT)
            if d.get("started_at")
            else None,
            duration_ms=d.get("duration_ms"),
            read=d.get("read", False),
        )


class NotificationStore:
    """通知持久化存储，读写 JSON 文件。"""

    def __init__(self, path: Path = _NOTIFICATIONS_FILE) -> None:
        self._path = path

    def load(self) -> list[NotificationRecord]:
        """从文件加载通知记录列表。"""
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("无法读取通知文件: %s", self._path, exc_info=True)
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("通知文件 JSON 格式错误: %s", self._path, exc_info=True)
            return []
        records: list[NotificationRecord] = []
        for i, d in enumerate(data):
            try:
                records.append(NotificationRecord.from_dict(d))
            except Exception:
                logger.warning("通知记录 #%d 解析失败，已跳过", i, exc_info=True)
        return records

    def save(self, records: list[NotificationRecord]) -> None:
        """将通知记录列表保存到文件。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    [r.to_dict() for r in records], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("保存通知文件失败: %s", self._path, exc_info=True)


class NotificationItem(Widget):
    """单条通知条目，点击展开/收起内容，标题行按钮操作已读/删除。"""

    DEFAULT_CSS = """
    NotificationItem {
        height: auto;
        padding: 0 1;
        margin: 0;
        border-bottom: solid $border-blurred;
    }
    NotificationItem:hover { background: $panel; }
    .item-header-row { height: 1; }
    .item-title { width: 1fr; height: 1; color: $text-muted; }
    .item-title.unread { color: $accent; text-style: bold; }
    .item-action { width: 3; height: 1; color: $text-muted; text-align: center; }
    .item-action:hover { color: $error; }
    .item-body { height: auto; color: $foreground; margin: 0; padding: 0; }
    """

    class Dismissed(Message):
        """单条通知被用户删除。"""

        def __init__(self, record_id: str) -> None:
            super().__init__()
            self.record_id = record_id

    class MarkRead(Message):
        """单条通知被标记为已读。"""

        def __init__(self, record_id: str) -> None:
            super().__init__()
            self.record_id = record_id

    def __init__(
        self, record: NotificationRecord, *, show_dismiss: bool = True
    ) -> None:
        super().__init__()
        self._record = record
        self._show_dismiss = show_dismiss
        self._expanded = False

    def compose(self) -> ComposeResult:
        r = self._record
        title_class = "item-title unread" if not r.read else "item-title"

        meta_parts: list[str] = []
        if r.started_at is not None:
            meta_parts.append(r.started_at.strftime("%H:%M:%S"))
        if r.duration_ms is not None:
            if r.duration_ms >= 60_000:
                mins, secs = divmod(r.duration_ms // 1000, 60)
                meta_parts.append(f"{mins}m{secs}s")
            elif r.duration_ms >= 1000:
                meta_parts.append(f"{r.duration_ms / 1000:.1f}s")
            else:
                meta_parts.append(f"{r.duration_ms}ms")
        meta_str = f"  [dim]{' · '.join(meta_parts)}[/dim]" if meta_parts else ""

        # 未读：右侧 ✓ 标记已读；已读：右侧 ✕ 删除
        action_label = "✕" if self._show_dismiss else "✓"
        with Horizontal(classes="item-header-row"):
            yield Static(f"{escape(r.job_name)}{meta_str}", classes=title_class)
            action_btn = Static(action_label, classes="item-action")
            action_btn.record_id = r.id  # type: ignore[attr-defined]
            yield action_btn
        yield Static(r.summary, classes="item-body", id=f"body-{r.id}", markup=False)

    def _toggle_expand(self) -> None:
        """切换展开/收起完整内容。"""
        try:
            body = self.query_one(f"#body-{self._record.id}", Static)
        except Exception:
            return
        self._expanded = not self._expanded
        body.update(self._record.content if self._expanded else self._record.summary)

    def on_click(self, event) -> None:
        """点击操作按钮执行已读/删除，点击其他区域展开/收起。"""
        try:
            btn = self.query_one(".item-action")
            if btn.region.contains_point(event.screen_offset):
                if self._show_dismiss:
                    self.post_message(self.Dismissed(self._record.id))
                else:
                    self.post_message(self.MarkRead(self._record.id))
                event.stop()
                return
        except Exception:
            logger.warning(
                "[NotificationItem] 操作按钮点击处理失败, record=%s",
                self._record.id,
                exc_info=True,
            )
        self._toggle_expand()


class _NotifTab(StrEnum):
    """通知面板的筛选标签页。"""

    UNREAD = "unread"
    READ = "read"


class _TabButton(Static):
    """可点击的标签按钮，点击时发送 TabClicked 消息。"""

    class TabClicked(Message):
        """标签被点击。"""

        def __init__(self, tab: _NotifTab) -> None:
            super().__init__()
            self.tab = tab

    def __init__(self, label: str, tab: _NotifTab, **kwargs) -> None:
        super().__init__(label, **kwargs)
        self._tab = tab

    def on_click(self, event) -> None:
        event.stop()
        self.post_message(self.TabClicked(self._tab))


class _ActionButton(Static):
    """标签栏右侧的操作按钮（一键已读 / 一键清空）。"""

    class Clicked(Message):
        """操作按钮被点击。"""

    def on_click(self, event) -> None:
        event.stop()
        self.post_message(self.Clicked())


class NotificationPanel(Widget):
    """右侧滑出式通知侧边栏，带持久化存储，支持已读/未读标签切换。"""

    # 最多保留 100 条通知，超出时丢弃最旧的记录
    MAX_RECORDS = 100

    # 当前激活的标签页
    active_tab: reactive[_NotifTab] = reactive(_NotifTab.UNREAD)

    DEFAULT_CSS = """
    NotificationPanel {
        layer: overlay;
        dock: right;
        width: 42;
        display: none;
        background: $surface;
        border-left: solid $border-blurred;
    }
    NotificationPanel.visible {
        display: block;
    }

    #notif-header-bar {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
        border-bottom: solid $border-blurred;
    }

    #notif-tab-bar {
        height: 1;
        padding: 0 1;
        margin-bottom: 1;
        background: $surface;
    }
    .notif-tab {
        width: auto;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    .notif-tab:hover {
        color: $foreground;
    }
    .notif-tab.active {
        color: $accent;
        text-style: bold;
    }
    #notif-action {
        width: 1fr;
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-align: right;
    }
    #notif-action:hover {
        color: $error;
    }

    #notif-scroll {
        height: 1fr;
        scrollbar-color: $scrollbar;
        scrollbar-color-hover: $accent;
    }

    #notif-empty {
        padding: 2 1;
        color: $text-muted;
        text-align: center;
    }

    .item-title { height: 1; color: $text-muted; }
    .item-title.unread { color: $accent; text-style: bold; }
    .item-body { color: $foreground; margin: 0; padding: 0; }
    """

    def __init__(self, store: NotificationStore | None = None) -> None:
        super().__init__()
        self._store = store or NotificationStore()
        self._records: list[NotificationRecord] = []

    @property
    def unread_count(self) -> int:
        """未读通知数量。"""
        return sum(1 for r in self._records if not r.read)

    @property
    def read_count(self) -> int:
        """已读通知数量。"""
        return sum(1 for r in self._records if r.read)

    def on_mount(self) -> None:
        """挂载时从文件加载历史通知。"""
        self._records = self._store.load()
        if len(self._records) > self.MAX_RECORDS:
            self._records = self._records[: self.MAX_RECORDS]
        self._rebuild_list()
        self.app.post_message(NotificationChanged(self.unread_count))

    def compose(self) -> ComposeResult:
        yield Static("⏰ 通知  [dim]Ctrl+N 收起[/dim]", id="notif-header-bar")
        with Horizontal(id="notif-tab-bar"):
            yield _TabButton(
                "未读", _NotifTab.UNREAD, classes="notif-tab active", id="tab-unread"
            )
            yield _TabButton("已读", _NotifTab.READ, classes="notif-tab", id="tab-read")
            yield _ActionButton("✓", id="notif-action")
        with VerticalScroll(id="notif-scroll"):
            yield Static("暂无通知", id="notif-empty")

    # ── 标签切换 ──

    def on__tab_button_tab_clicked(self, event: _TabButton.TabClicked) -> None:
        """处理标签点击切换。"""
        self.active_tab = event.tab

    def on__action_button_clicked(self, _event: _ActionButton.Clicked) -> None:
        """处理操作按钮点击：未读标签下全部已读，已读标签下清空已读。"""
        if self.active_tab == _NotifTab.UNREAD:
            for r in self._records:
                r.read = True
        else:
            self._records = [r for r in self._records if not r.read]
        self._save_and_refresh()

    def watch_active_tab(self, new_tab: _NotifTab) -> None:
        """标签页切换时更新样式和列表。"""
        try:
            tab_unread = self.query_one("#tab-unread")
            tab_read = self.query_one("#tab-read")
            action = self.query_one("#notif-action", Static)
        except Exception:
            return
        tab_unread.set_classes(
            "notif-tab active" if new_tab == _NotifTab.UNREAD else "notif-tab"
        )
        tab_read.set_classes(
            "notif-tab active" if new_tab == _NotifTab.READ else "notif-tab"
        )
        action.update("✓" if new_tab == _NotifTab.UNREAD else "✕")
        self._update_tab_labels()
        self._rebuild_list()

    def _update_tab_labels(self) -> None:
        """更新标签文本，显示对应数量。"""
        try:
            tab_unread = self.query_one("#tab-unread", Static)
            tab_read = self.query_one("#tab-read", Static)
        except Exception:
            return
        unread = self.unread_count
        read = self.read_count
        tab_unread.update(f"未读{f' ({unread})' if unread else ''}")
        tab_read.update(f"已读{f' ({read})' if read else ''}")

    # ── 公共 API ──

    def add_notification(
        self,
        job_name: str,
        output: str,
        *,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """新增一条通知记录，刷新面板并持久化。"""
        record = NotificationRecord.create(
            job_name, output, started_at=started_at, duration_ms=duration_ms
        )
        self._records.insert(0, record)
        if len(self._records) > self.MAX_RECORDS:
            self._records = self._records[: self.MAX_RECORDS]
        self._save_and_refresh()

    def toggle_panel(self) -> None:
        """切换面板展开/收起状态。"""
        if self.has_class("visible"):
            self.remove_class("visible")
        else:
            self.add_class("visible")

    # ── 内部逻辑 ──

    def _save_and_refresh(self) -> None:
        """保存到文件、重建列表、通知铃铛更新。"""
        self._store.save(self._records)
        self._rebuild_list()
        self._update_tab_labels()
        self.app.post_message(NotificationChanged(self.unread_count))

    def _filtered_records(self) -> list[NotificationRecord]:
        """根据当前标签页筛选通知记录。"""
        if self.active_tab == _NotifTab.UNREAD:
            return [r for r in self._records if not r.read]
        return [r for r in self._records if r.read]

    def _rebuild_list(self) -> None:
        """根据当前标签页重建通知列表内容。"""
        scroll = self.query_one("#notif-scroll")
        scroll.query(NotificationItem).remove()
        empty = scroll.query_one("#notif-empty", Static)
        filtered = self._filtered_records()
        show_dismiss = self.active_tab == _NotifTab.READ
        if filtered:
            empty.display = False
            scroll.mount_all(
                NotificationItem(r, show_dismiss=show_dismiss) for r in filtered
            )
        else:
            empty.display = True
            label = (
                "暂无未读通知"
                if self.active_tab == _NotifTab.UNREAD
                else "暂无已读通知"
            )
            empty.update(label)

    def on_notification_item_dismissed(self, event: NotificationItem.Dismissed) -> None:
        """删除单条通知。"""
        self._records = [r for r in self._records if r.id != event.record_id]
        self._save_and_refresh()

    def on_notification_item_mark_read(self, event: NotificationItem.MarkRead) -> None:
        """标记单条通知为已读。"""
        for r in self._records:
            if r.id == event.record_id:
                r.read = True
                break
        self._save_and_refresh()

    def on_key(self, event) -> None:
        if event.key == "escape" and self.has_class("visible"):
            self.remove_class("visible")
            event.stop()


class NotificationChanged(Message):
    """通知数量变化消息，用于更新 InputBar 中的铃铛指示器。"""

    def __init__(self, unread: int) -> None:
        super().__init__()
        self.unread = unread

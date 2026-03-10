"""定时任务管理界面

展示所有定时任务列表，支持搜索过滤和删除操作。
基于 ListScreen 基类实现，扩展 Delete 键删除功能。
删除确认使用独立的 ConfirmDialog 弹窗，避免按键冲突。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Static

from lumi.agents.cron.models import Job, ScheduleType
from lumi.tui.screens.list_screen import ListScreen
from lumi.utils.logger import logger


def _format_schedule(job: Job) -> str:
    """将调度规则格式化为人性化的摘要文本。

    Args:
        job: 定时任务对象。

    Returns:
        人性化的调度描述字符串。
    """
    s = job.schedule
    match s.type:
        case ScheduleType.AT:
            try:
                dt = datetime.fromisoformat(s.value)
                return f"一次性 {dt:%m-%d %H:%M}"
            except ValueError:
                return f"一次性 {s.value}"
        case ScheduleType.INTERVAL:
            return f"每 {s.value} 执行一次"
        case ScheduleType.CRON:
            return f"cron {s.value}"


class _ConfirmDialog(ModalScreen[bool]):
    """删除确认弹窗，返回 True 确认 / False 取消。"""

    DEFAULT_CSS = """
    _ConfirmDialog {
        align: center middle;
    }
    _ConfirmDialog > Vertical {
        width: 50;
        height: auto;
        background: $surface;
        border: round $warning;
        border-title-style: bold;
        border-title-color: $warning;
        padding: 1 2;
    }
    _ConfirmDialog .dialog-msg {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    _ConfirmDialog .dialog-buttons {
        width: 100%;
        height: auto;
        align-horizontal: center;
    }
    _ConfirmDialog .dialog-buttons Button {
        margin: 0 1;
        min-width: 16;
        height: 3;
        background: $surface-darken-1;
        color: $text;
        border: tall $surface-lighten-1;
        text-style: none;
    }
    _ConfirmDialog .dialog-buttons Button:focus {
        background: $error;
        color: $text;
        text-style: bold;
        border: tall $error-darken-1;
    }
    _ConfirmDialog .dialog-buttons Button.-active {
        background: $error-darken-1;
        border: tall $error-darken-2;
    }
    """

    def __init__(self, job_name: str) -> None:
        super().__init__()
        self._job_name = job_name

    def compose(self) -> ComposeResult:
        container = Vertical()
        container.border_title = "确认删除"
        with container:
            yield Static(
                f"确定要删除「{self._job_name}」吗？",
                classes="dialog-msg",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("确认", variant="default", id="btn-confirm")
                yield Button("取消", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """按钮点击处理。"""
        self.dismiss(event.button.id == "btn-confirm")

    def _on_key(self, event: Key) -> None:
        """Esc 快捷取消。"""
        if event.key == "escape":
            self.dismiss(False)
            event.prevent_default()
            event.stop()


class _CronJobItem(Widget):
    """单个定时任务条目，使用 Horizontal 布局确保左右不换行。

    第一行：指示符 + 状态 + 名称(ID)（左） / ⏱ 调度摘要（右）
    第二行：prompt 预览
    """

    DEFAULT_CSS = """
    _CronJobItem {
        width: 100%;
        height: auto;
        padding: 0 2;
        border-bottom: solid $surface-darken-2;
    }
    _CronJobItem.selected {
        background: $accent 20%;
    }
    .job-row {
        width: 100%;
        height: 1;
    }
    .job-left {
        width: 1fr;
        height: 1;
    }
    .job-right {
        width: auto;
        height: 1;
        color: $text-muted;
    }
    .job-prompt {
        width: 100%;
        height: 1;
        color: $text-muted;
        text-style: italic;
        padding-left: 2;
    }
    """

    def __init__(self, job: Job, index: int, *, selected: bool = False) -> None:
        super().__init__()
        self._job = job
        self._index = index
        self._selected = selected

    def compose(self) -> ComposeResult:
        status = "✅" if self._job.enabled else "⏸️"
        short_id = self._job.id[:8]
        indicator = "▸" if self._selected else " "
        sched = _format_schedule(self._job)

        prompt = self._job.prompt.replace("\n", " ").strip()
        if len(prompt) > 60:
            prompt = prompt[:57] + "..."

        with Horizontal(classes="job-row"):
            yield Static(
                f"{indicator} {status} {self._job.name}({short_id})",
                classes="job-left",
                id=f"left-{self._index}",
            )
            yield Static(f"⏱ {sched}", classes="job-right")
        yield Static(prompt, classes="job-prompt", id=f"prompt-{self._index}")

    @property
    def job(self) -> Job:
        return self._job

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态并更新指示符。"""
        self._selected = selected
        self.set_class(selected, "selected")
        indicator = "▸" if selected else " "
        status = "✅" if self._job.enabled else "⏸️"
        short_id = self._job.id[:8]
        from textual.css.query import NoMatches

        try:
            left = self.query_one(f"#left-{self._index}", Static)
            left.update(f"{indicator} {status} {self._job.name}({short_id})")
        except NoMatches:
            logger.debug("[_CronJobItem] Widget #left-%s not found", self._index)


class CronScreen(ListScreen[Job]):
    """定时任务管理界面

    显示所有定时任务，支持搜索、选中高亮、Delete 弹窗确认删除。
    删除后留在界面内刷新列表，关闭时 dismiss(None)。
    如果有任务被删除，dismiss 返回 "changed" 通知调用方。
    """

    def __init__(
        self,
        jobs: list[Job],
        on_delete: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """初始化定时任务管理界面。

        Args:
            jobs: 所有定时任务列表。
            on_delete: 删除回调，接收 job_id，执行实际删除逻辑。
        """
        super().__init__(jobs)
        self._on_delete = on_delete
        self._has_deleted: bool = False

    @property
    def screen_title(self) -> str:
        return "Cron Jobs"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Delete 删除 · Esc close"

    @property
    def empty_text(self) -> str:
        return "当前没有任何定时任务"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的任务"

    def match_filter(self, item: Job, query: str) -> bool:
        return (
            query in item.name.lower()
            or query in item.id.lower()
            or query in item.prompt.lower()
            or query in item.schedule.value.lower()
        )

    def make_item_widget(self, item: Job, index: int) -> Widget:
        return _CronJobItem(item, index, selected=(index == self._selected_index))

    def get_dismiss_value(self, item: Job) -> str:
        return item.id

    # ── 删除扩展 ──

    def _on_key(self, event: Key) -> None:
        """扩展基类键盘处理，增加 Delete 键删除。"""
        if event.key == "delete":
            self._request_delete()
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

    def action_cancel(self) -> None:
        """关闭界面，返回是否有删除操作。"""
        self.dismiss("changed" if self._has_deleted else None)

    def _request_delete(self) -> None:
        """弹出确认弹窗请求删除选中任务。"""
        if not self._filtered or self._selected_index >= len(self._filtered):
            return
        job = self._filtered[self._selected_index]
        self.app.push_screen(_ConfirmDialog(job.name), callback=self._on_confirm_result)

    def _on_confirm_result(self, confirmed: bool) -> None:
        """确认弹窗回调：确认则执行删除并刷新列表，取消则留在列表。"""
        if not confirmed:
            return
        if not self._filtered or self._selected_index >= len(self._filtered):
            return
        job = self._filtered[self._selected_index]

        # 从内部列表中移除
        self._all_items = [j for j in self._all_items if j.id != job.id]
        self._filtered = [j for j in self._filtered if j.id != job.id]
        self._has_deleted = True

        # 调整选中索引
        if self._selected_index >= len(self._filtered):
            self._selected_index = max(0, len(self._filtered) - 1)

        # 调用外部删除回调（异步）
        if self._on_delete is not None:
            task = asyncio.create_task(self._on_delete(job.id))
            task.add_done_callback(self._on_delete_task_done)

        # 刷新列表和标题
        self.call_later(self._refresh_after_delete)

    @staticmethod
    def _on_delete_task_done(task: asyncio.Task) -> None:
        """删除任务完成回调，记录异常。"""
        if not task.cancelled() and (exc := task.exception()):
            logger.error("[CronScreen] 删除任务失败: %s", exc, exc_info=exc)

    async def _refresh_after_delete(self) -> None:
        """删除后刷新列表和标题。"""
        await self._render_list()
        self._update_title()

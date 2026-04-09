"""后台任务列表界面

展示运行中的后台任务，支持搜索、查看详情和停止任务。
基于 ListScreen 基类实现。
"""

from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Rule, Static

from lumi.agents.tools.task_registry import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    get_task_registry,
)
from lumi.tui.screens.list_screen import ListScreen
from lumi.utils.logger import logger


def _status_icon(status: TaskStatus) -> tuple[str, str]:
    """返回 (图标, 样式) 元组。"""
    match status:
        case TaskStatus.RUNNING:
            return "●", "bold cyan"
        case TaskStatus.COMPLETED:
            return "✓", "bold green"
        case TaskStatus.FAILED:
            return "✗", "bold red"
        case TaskStatus.TIMED_OUT:
            return "⏱", "bold yellow"
        case _:
            return "?", "dim"


def _format_duration(started_at: float, completed_at: float | None = None) -> str:
    """格式化持续时间。"""
    elapsed = (completed_at or time.time()) - started_at
    if elapsed < 60:
        return f"{int(elapsed)}s"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
    return f"{int(elapsed // 3600)}h{int((elapsed % 3600) // 60)}m"


class _TaskItem(Static):
    """单个后台任务条目。"""

    DEFAULT_CSS = """
    _TaskItem {
        width: 100%;
        height: 2;
        padding: 0 2;
        color: $foreground;
    }
    _TaskItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, entry: BackgroundTaskEntry, index: int) -> None:
        self._entry = entry
        self._index = index

        icon, icon_style = _status_icon(entry.status)

        text = Text()
        text.append(f"{icon} ", style=icon_style)
        text.append(entry.label, style="bold")
        text.append(f"  {entry.status}", style="dim")
        text.append(f"\n  {entry.task_id}", style="dim")
        super().__init__(text, markup=False)

    @property
    def entry(self) -> BackgroundTaskEntry:
        return self._entry

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        self.set_class(selected, "selected")


class _BgDetailScreen(ModalScreen[None]):
    """后台任务详情弹窗。"""

    DEFAULT_CSS = """
    _BgDetailScreen {
        align: center middle;
    }
    _BgDetailScreen > Vertical {
        width: 90;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }
    _BgDetailScreen .detail-info {
        color: $foreground;
        width: 100%;
        padding: 0 0 1 0;
    }
    _BgDetailScreen .detail-content {
        height: auto;
        max-height: 60vh;
        padding: 0 1;
    }
    _BgDetailScreen .detail-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def __init__(self, entry: BackgroundTaskEntry) -> None:
        super().__init__()
        self._entry = entry
        self._container: Vertical | None = None

    def compose(self) -> ComposeResult:
        e = self._entry
        self._container = Vertical()
        with self._container:
            yield Static("", id="bg-detail-info", classes="detail-info")
            if e.prompt:
                yield Rule()
                yield Static("[bold]Prompt[/]")
                with VerticalScroll(classes="detail-content"):
                    yield Static(e.prompt, markup=False)
            yield Rule()
            yield Static("", id="bg-detail-hint", classes="detail-hint")

    def on_mount(self) -> None:
        self._refresh()
        if self._entry.status == TaskStatus.RUNNING:
            self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        """每秒刷新动态内容（标题栏 duration、info、hint）。"""
        e = self._entry
        icon, _ = _status_icon(e.status)
        duration = _format_duration(e.started_at, e.completed_at)

        if self._container:
            self._container.border_title = f"{e.label} · {icon} {e.status} · {duration}"

        info_lines = [
            f"Task ID:  {e.task_id}",
            f"Kind:     {e.kind}",
            f"Status:   {e.status}",
            f"Duration: {duration}",
            f"Output:   {e.output_file.resolve()}",
        ]
        if e.agent_name:
            info_lines.insert(2, f"Agent:    {e.agent_name}")
        if e.exit_code is not None:
            info_lines.append(f"Exit Code: {e.exit_code}")
        if e.error:
            info_lines.append(f"Error:    {e.error}")

        self.query_one("#bg-detail-info", Static).update("\n".join(info_lines))

        hint = "Esc back · x stop" if e.status == TaskStatus.RUNNING else "Esc back"
        self.query_one("#bg-detail-hint", Static).update(hint)

    def _on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()
            event.stop()
        elif event.key == "x" and self._entry.status == TaskStatus.RUNNING:
            self._stop_task()
            self.dismiss(None)
            event.prevent_default()
            event.stop()

    def _stop_task(self) -> None:
        """停止当前任务。"""
        entry = self._entry
        if entry.kind == TaskKind.AGENT:
            cancelled = get_task_registry().cancel_agent_task(entry.task_id)
            if not cancelled:
                logger.warning(
                    "[BgScreen] cancel_agent_task 返回 False (task_id=%s)",
                    entry.task_id,
                )
        elif entry.kind == TaskKind.BASH:
            asyncio.create_task(_stop_bash(entry.task_id))


class BgScreen(ListScreen[BackgroundTaskEntry]):
    """后台任务列表界面。"""

    @property
    def screen_title(self) -> str:
        return "Background"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter 查看 · x stop · Esc close"

    @property
    def empty_text(self) -> str:
        return "暂无运行中的后台任务"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的任务"

    def match_filter(self, item: BackgroundTaskEntry, query: str) -> bool:
        return (
            query in item.task_id.lower()
            or query in item.label.lower()
            or (item.agent_name and query in item.agent_name.lower())
        )

    def make_item_widget(self, item: BackgroundTaskEntry, index: int) -> Widget:
        return _TaskItem(item, index)

    def get_dismiss_value(self, item: BackgroundTaskEntry) -> str:
        return item.task_id

    def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            if (entry := self._selected_item) is not None:
                self.app.push_screen(_BgDetailScreen(entry))
            event.prevent_default()
            event.stop()
            return
        if event.key == "x":
            if (entry := self._selected_item) is not None:
                self._stop_task(entry)
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

    def _stop_task(self, entry: BackgroundTaskEntry) -> None:
        """停止选中的任务。"""
        if entry.status != TaskStatus.RUNNING:
            return
        if entry.kind == TaskKind.AGENT:
            get_task_registry().cancel_agent_task(entry.task_id)
        elif entry.kind == TaskKind.BASH:
            asyncio.create_task(_stop_bash(entry.task_id))
        logger.info("[BgScreen] 已请求停止任务 %s", entry.task_id)


async def _stop_bash(task_id: str) -> None:
    """异步停止 Bash 后台任务。"""
    from lumi.agents.tools.session import get_session_manager

    session_mgr = get_session_manager()
    if session_mgr.has_bg_manager:
        try:
            await session_mgr.bg_manager.cancel_task(task_id)
        except (OSError, ProcessLookupError) as e:
            logger.warning("[BgScreen] 停止 Bash 任务失败 %s: %s", task_id, e)
        except Exception:
            logger.error(
                "[BgScreen] 停止 Bash 任务出现意外错误 %s", task_id, exc_info=True
            )

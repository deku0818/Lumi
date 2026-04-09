"""Rewind 选择界面

展示当前会话的 checkpoint 列表，用户可通过 ↑↓ 选择、Enter 确认回退、Esc 取消。
基于 ListScreen 基类实现。
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.agents.tools.checkpoint import CheckpointInfo
from lumi.tui.screens.list_screen import ListScreen


class _CheckpointItem(Static):
    """单个 checkpoint 条目"""

    DEFAULT_CSS = """
    _CheckpointItem {
        width: 100%;
        height: auto;
        padding: 0 2;
        color: $foreground;
    }
    _CheckpointItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, info: CheckpointInfo, index: int) -> None:
        self._info = info
        self._index = index

        label = info.label.replace("\n", " ").strip()
        if len(label) > 80:
            label = label[:77] + "..."

        text = Text()
        text.append(f"› {label}\n", style="bold")

        # diff 统计行
        if info.files_changed > 0:
            text.append(
                f"  {info.files_changed} file{'s' if info.files_changed > 1 else ''} changed",
                style="dim",
            )
            if info.insertions > 0:
                text.append(f" +{info.insertions}", style="green")
            if info.deletions > 0:
                text.append(f" -{info.deletions}", style="red")
            text.append("\n")

        text.append(
            f"  {info.display_time} · {info.checkpoint_id}",
            style="dim",
        )
        super().__init__(text, markup=False)

    @property
    def info(self) -> CheckpointInfo:
        return self._info

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        self.set_class(selected, "selected")


class RewindScreen(ListScreen[CheckpointInfo]):
    """Rewind checkpoint 选择界面

    显示当前会话的 checkpoint 列表（按时间正序，最旧在前），
    默认选中最后一项（最新 checkpoint），用户选择后返回 commit_hash。
    """

    def __init__(self, items: list[CheckpointInfo], *, initial_index: int = -1) -> None:
        super().__init__(items, initial_index=initial_index)

    @property
    def screen_title(self) -> str:
        return "Rewind"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter rewind · Esc cancel"

    @property
    def empty_text(self) -> str:
        return "No checkpoints available"

    @property
    def no_match_text(self) -> str:
        return "No checkpoints found"

    def match_filter(self, item: CheckpointInfo, query: str) -> bool:
        return query in item.label.lower() or query in item.checkpoint_id.lower()

    def make_item_widget(self, item: CheckpointInfo, index: int) -> Widget:
        return _CheckpointItem(item, index)

    def get_dismiss_value(self, item: CheckpointInfo) -> str:
        return item.commit_hash

"""通用列表弹窗基类

提供居中 ModalScreen 弹窗、搜索过滤、↑↓ 键盘导航、Enter 确认、Esc 取消
等通用交互逻辑。子类只需实现少量抽象方法即可获得一致的列表选择体验。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Generic, TypeVar

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, Rule, Static

T = TypeVar("T")

# Widget protocol: make_item_widget 返回的 widget 需满足此协议
# - index: int 属性
# - set_selected(bool) 方法


class ListScreen(ModalScreen[str | None], Generic[T]):
    """通用列表弹窗基类。

    泛型参数 T 为列表项的数据类型（如 SessionSummary、Job、SkillConfig）。
    子类需实现以下抽象方法/属性来定制行为：

    - screen_title: 弹窗标题
    - hint_text: 底部操作提示
    - empty_text: 列表为空时的提示
    - no_match_text: 搜索无匹配时的提示
    - match_filter(item, query): 搜索匹配逻辑
    - make_item_widget(item, index): 创建列表项 widget
    - get_dismiss_value(item): Enter 确认时返回的值
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("ctrl+c", "quit_app", "Quit"),
    ]

    DEFAULT_CSS = """
    ListScreen {
        align: center middle;
    }

    ListScreen > Vertical {
        width: 80;
        height: 80%;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }

    ListScreen .ls-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        width: 100%;
    }

    ListScreen .ls-search {
        margin-bottom: 1;
    }

    ListScreen .ls-list {
        height: 1fr;
    }

    ListScreen .ls-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }

    ListScreen .ls-empty {
        text-align: center;
        color: $text-muted;
        width: 100%;
        padding: 2 0;
    }
    """

    def __init__(self, items: list[T], *, initial_index: int = 0) -> None:
        """初始化列表弹窗。

        Args:
            items: 完整数据列表。
            initial_index: 初始选中项索引，-1 表示最后一项。
        """
        super().__init__()
        self._all_items: list[T] = list(items)
        self._filtered: list[T] = list(items)
        if initial_index < 0:
            initial_index = max(0, len(items) + initial_index)
        self._selected_index: int = initial_index
        self._item_widgets: list[Widget] = []

    # ── 子类必须实现 ──

    @property
    @abstractmethod
    def screen_title(self) -> str:
        """弹窗标题文本。"""

    @property
    @abstractmethod
    def hint_text(self) -> str:
        """底部操作提示文本。"""

    @property
    def empty_text(self) -> str:
        """列表为空时的提示。"""
        return "暂无数据"

    @property
    def no_match_text(self) -> str:
        """搜索无匹配时的提示。"""
        return "没有匹配的结果"

    @abstractmethod
    def match_filter(self, item: T, query: str) -> bool:
        """判断 item 是否匹配搜索关键词。

        Args:
            item: 列表项数据。
            query: 小写搜索关键词。

        Returns:
            是否匹配。
        """

    @abstractmethod
    def make_item_widget(self, item: T, index: int) -> Widget:
        """为列表项创建 widget。

        widget 需要暴露 `index` 属性和 `set_selected(bool)` 方法。

        Args:
            item: 列表项数据。
            index: 在 _filtered 中的索引。

        Returns:
            列表项 widget。
        """

    @abstractmethod
    def get_dismiss_value(self, item: T) -> str:
        """Enter 确认时返回的值。

        Args:
            item: 选中的列表项数据。

        Returns:
            传给 dismiss() 的字符串。
        """

    # ── 选中项访问 ──

    @property
    def _selected_item(self) -> T | None:
        """返回当前选中的列表项，越界或列表为空时返回 None。"""
        if self._filtered and 0 <= self._selected_index < len(self._filtered):
            return self._filtered[self._selected_index]
        return None

    def _clamp_selected_index(self) -> None:
        """将选中索引限制在有效范围内（删除列表项后调用）。"""
        if self._selected_index >= len(self._filtered):
            self._selected_index = max(0, len(self._filtered) - 1)

    # ── 布局 ──

    def _format_title(self, filtered_count: int, total_count: int) -> str:
        """格式化标题文本（含计数）。"""
        return f"{self.screen_title} ({filtered_count} of {total_count})"

    def compose(self) -> ComposeResult:
        """渲染界面。"""
        container = Vertical()
        container.border_title = self.screen_title
        total = len(self._all_items)
        with container:
            yield Label(
                self._format_title(total, total),
                classes="ls-title",
                id="ls-title",
            )
            yield Input(placeholder="Search...", classes="ls-search", id="ls-search")
            yield Rule()
            yield VerticalScroll(id="ls-list", classes="ls-list")
            yield Rule()
            yield Static(self.hint_text, classes="ls-hint")

    async def on_mount(self) -> None:
        """挂载后渲染列表并聚焦搜索框。"""
        await self._render_list()
        self.query_one("#ls-search", Input).focus()

    # ── 搜索 ──

    async def on_input_changed(self, event: Input.Changed) -> None:
        """搜索框内容变化时过滤列表。"""
        query = event.value.strip().lower()
        if query:
            self._filtered = [
                item for item in self._all_items if self.match_filter(item, query)
            ]
        else:
            self._filtered = list(self._all_items)
        self._selected_index = 0
        await self._render_list()
        self._update_title()

    # ── 列表渲染 ──

    async def _render_list(self) -> None:
        """重新渲染列表。"""
        container = self.query_one("#ls-list", VerticalScroll)
        await container.remove_children()
        self._item_widgets.clear()

        if not self._filtered:
            hint = self.no_match_text if self._all_items else self.empty_text
            await container.mount(Static(hint, classes="ls-empty"))
            return

        for i, item in enumerate(self._filtered):
            widget = self.make_item_widget(item, i)
            self._item_widgets.append(widget)
            await container.mount(widget)

        self._update_selection()

    def _update_selection(self) -> None:
        """更新选中状态高亮。"""
        for widget in self._item_widgets:
            idx = getattr(widget, "index", -1)
            widget.set_selected(idx == self._selected_index)  # type: ignore[attr-defined]
        if self._item_widgets and 0 <= self._selected_index < len(self._item_widgets):
            self._item_widgets[self._selected_index].scroll_visible()

    def _update_title(self) -> None:
        """更新标题计数。"""
        title = self.query_one("#ls-title", Label)
        title.update(self._format_title(len(self._filtered), len(self._all_items)))

    # ── 键盘导航 ──

    def _on_key(self, event: Key) -> None:
        """处理键盘事件：↑↓ 导航、Enter 确认、Esc 取消。

        子类可 override 此方法扩展按键处理（如 Delete），
        未处理的按键应调用 super()._on_key(event)。
        """
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
                if (item := self._selected_item) is not None:
                    self.dismiss(self.get_dismiss_value(item))
                event.prevent_default()
                event.stop()

    def action_cancel(self) -> None:
        """取消选择。"""
        self.dismiss(None)

    async def action_quit_app(self) -> None:
        """ctrl+c 退出程序。"""
        self.app.exit()

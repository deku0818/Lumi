"""审批组件基类 — 键盘驱动的选项选择器

提取 ToolApproval 和 PlanApproval 的共享逻辑：
- 选项状态管理（_selected 索引、上下移动、确认/取消）
- 选项渲染（Rich Text，高亮当前选中项）
- 滚动委派（将 app 级滚动转发到内容容器）
- 挂载后自动获取焦点和边框颜色设置
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Static

from lumi.tui.theme import get_color


class BaseApproval(Vertical):
    """审批组件基类 - 键盘驱动的列表选择器

    子类需要：
    1. 定义 DEFAULT_CSS
    2. 实现 compose()
    3. 设置 _options, _option_color_roles, _cancel_key, _options_selector, _content_selector
    """

    can_focus = True

    class Decided(Message):
        """用户做出审批决定"""

        def __init__(self, decision: str) -> None:
            super().__init__()
            self.decision = decision

    def __init__(
        self,
        *,
        options: tuple[dict[str, str], ...],
        option_color_roles: dict[str, str],
        cancel_key: str,
        options_selector: str,
        content_selector: str,
        classes: str = "",
    ) -> None:
        super().__init__(classes=classes)
        self._options = options
        self._option_color_roles = option_color_roles
        self._cancel_key = cancel_key
        self._options_selector = options_selector
        self._content_selector = content_selector
        self._selected = 0

    def on_mount(self) -> None:
        """挂载后自动获取焦点，并设置边框颜色。"""
        self.focus()
        border_color = get_color("border_separator")
        self.styles.border_title_color = border_color
        self.styles.border_subtitle_color = border_color

    def on_key(self, event) -> None:
        """键盘事件处理"""
        if event.key == "up":
            self._selected = (self._selected - 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "down":
            self._selected = (self._selected + 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "enter":
            decision = self._options[self._selected]["key"]
            self.post_message(self.Decided(decision))
            self.call_later(self.remove)
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decided(self._cancel_key))
            self.call_later(self.remove)
            event.stop()

    def scroll_content(self, direction: str) -> None:
        """滚动内容区域，由 app 级快捷键委派调用。"""
        try:
            container = self.query_one(self._content_selector)
        except NoMatches:
            return
        if direction == "up":
            container.scroll_up(animate=False)
        elif direction == "down":
            container.scroll_down(animate=False)
        elif direction == "page_up":
            container.scroll_page_up(animate=False)
        elif direction == "page_down":
            container.scroll_page_down(animate=False)

    def _render_options(self, max_label_len: int = 0) -> Text:
        """渲染选项列表，高亮当前选中项。

        Args:
            max_label_len: 标签最大长度，0 表示不截断
        """
        result = Text()
        for i, opt in enumerate(self._options):
            if i > 0:
                result.append("\n")
            key = opt["key"]
            label = opt.get("label", key)
            if max_label_len > 0 and len(label) > max_label_len:
                label = label[: max_label_len - 1] + "…"
            color = get_color(self._option_color_roles.get(key, "foreground"))
            if i == self._selected:
                result.append(f" ❯ {label}", style=f"bold {color}")
            else:
                result.append(f"   {label}")
        return result

    def _refresh_options(self) -> None:
        """刷新选项显示"""
        self.query_one(self._options_selector, Static).update(self._render_options())

"""可折叠任务列表面板

默认收起为一行摘要，点击或快捷键展开完整列表。
位于输入框上方，替代原来的 Static #todos-bar。
"""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Static

from lumi.tui.renderers.todos import build_todos_text
from lumi.tui.theme import get_color

# 折叠/展开图标
_ICON_COLLAPSED = "▸"
_ICON_EXPANDED = "▾"

# 各状态对应的图标（与 renderers/todos.py 保持一致）
_STATUS_ICONS: dict[str, str] = {
    "pending": "□",
    "in_progress": "■",
    "completed": "✓",
}


def _build_summary(todos: list[dict]) -> Text:
    """构建一行摘要文本：▸ Tasks: ■ 当前任务名 (完成数/总数)"""
    total = len(todos)
    completed = sum(1 for t in todos if t.get("status") == "completed")

    # 找到当前进行中的任务，没有则取第一个未完成的
    current = ""
    for t in todos:
        if t.get("status") == "in_progress":
            current = t.get("content", "")
            break
    if not current:
        for t in todos:
            if t.get("status") != "completed":
                current = t.get("content", "")
                break

    result = Text()
    accent = get_color("accent")
    muted = get_color("text_muted")

    result.append(f"  {_ICON_COLLAPSED} ", style=muted)
    result.append("Tasks: ", style=muted)
    if current:
        icon = _STATUS_ICONS.get("in_progress", "■")
        result.append(f"{icon} ", style=accent)
        # 截断过长的任务名
        display = current if len(current) <= 40 else current[:37] + "…"
        result.append(display, style=accent)
        result.append(f" ({completed}/{total})", style=muted)
    else:
        result.append(f"({completed}/{total})", style=muted)

    return result


def _build_expanded(todos: list[dict]) -> Text:
    """构建展开状态的完整文本：摘要行 + 任务列表"""
    total = len(todos)
    completed = sum(1 for t in todos if t.get("status") == "completed")

    # 找到当前进行中的任务
    current = ""
    for t in todos:
        if t.get("status") == "in_progress":
            current = t.get("content", "")
            break
    if not current:
        for t in todos:
            if t.get("status") != "completed":
                current = t.get("content", "")
                break

    result = Text()
    accent = get_color("accent")
    muted = get_color("text_muted")

    # 摘要行（展开图标）
    result.append(f"  {_ICON_EXPANDED} ", style=muted)
    result.append("Tasks: ", style=muted)
    if current:
        icon = _STATUS_ICONS.get("in_progress", "■")
        result.append(f"{icon} ", style=accent)
        display = current if len(current) <= 40 else current[:37] + "…"
        result.append(display, style=accent)
        result.append(f" ({completed}/{total})", style=muted)
    else:
        result.append(f"({completed}/{total})", style=muted)

    # 任务列表
    result.append("\n")
    result.append(build_todos_text(todos))

    return result


class TodosBar(Static):
    """可折叠的任务列表面板。

    默认收起显示一行摘要，点击或按 Enter 切换展开/收起。
    run 结束时若全部完成则由 app._finish_run 清除。
    """

    can_focus = True

    DEFAULT_CSS = """
    TodosBar {
        display: none;
        margin: 0 0 0 1;
        padding: 0 1;
        height: auto;
        max-height: 12;
        color: $text-muted;
    }
    TodosBar.-visible {
        display: block;
    }
    TodosBar:focus {
        text-style: reverse;
    }
    """

    BINDINGS = [
        Binding("enter", "toggle_expand", "展开/收起", show=False),
    ]

    expanded: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__("", id="todos-bar")
        self._todos: list[dict] = []

    def update_todos(self, todos: list[dict]) -> None:
        """更新任务列表数据并重新渲染。

        Args:
            todos: 任务列表，每项包含 content 和 status 字段
        """
        self._todos = todos
        if not todos:
            self.update("")
            self.remove_class("-visible")
            self.expanded = False
            return
        self.add_class("-visible")
        self._refresh_content()

    def clear(self) -> None:
        """隐藏并清空面板。"""
        self._todos = []
        self.expanded = False
        self.update("")
        self.remove_class("-visible")

    @property
    def is_all_done(self) -> bool:
        """所有任务是否已完成。"""
        return bool(self._todos) and all(
            t.get("status") == "completed" for t in self._todos
        )

    def on_click(self) -> None:
        """点击切换展开/收起。"""
        if self._todos:
            self.expanded = not self.expanded

    def action_toggle_expand(self) -> None:
        """快捷键切换展开/收起。"""
        if self._todos:
            self.expanded = not self.expanded

    def watch_expanded(self, value: bool) -> None:
        """expanded 变化时重新渲染。"""
        self._refresh_content()

    def _refresh_content(self) -> None:
        """根据当前状态渲染内容。"""
        if not self._todos:
            return
        if self.expanded:
            self.update(_build_expanded(self._todos))
        else:
            self.update(_build_summary(self._todos))

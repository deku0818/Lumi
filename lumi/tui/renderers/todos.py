"""任务列表工具（todos）渲染器

标题格式: todos（无关键参数）
参数区域: 以待办事项列表形式展示
输出区域: 简单文本展示

公开 API:
    build_todos_text(todos) — 供 #todos-bar 面板复用
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.theme import get_color

# 各状态对应的图标
_STATUS_ICONS: dict[str, str] = {
    "pending": "□",
    "in_progress": "■",
    "completed": "✓",
}

# 各状态对应的语义颜色角色
_STATUS_COLOR_ROLES: dict[str, str] = {
    "pending": "text_muted",
    "in_progress": "accent",
    "completed": "success",
}


@register_renderer("todos")
class TodosRenderer(BaseRenderer):
    """todos 工具渲染器"""

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以待办事项列表形式展示任务"""
        todos: list[dict] = args.get("todos", [])
        if not todos:
            return Static("", markup=False)
        return Static(build_todos_text(todos))

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：各状态任务数统计"""
        if is_error:
            return "Error"
        todos: list[dict] = args.get("todos", [])
        if not todos:
            return "Done"
        counts: dict[str, int] = {"pending": 0, "in_progress": 0, "completed": 0}
        for todo in todos:
            status = todo.get("status", "pending")
            if status in counts:
                counts[status] += 1
        parts: list[str] = []
        for key, label in (
            ("completed", "completed"),
            ("in_progress", "in progress"),
            ("pending", "pending"),
        ):
            if counts[key]:
                parts.append(f"{counts[key]} {label}")
        return ", ".join(parts) if parts else "No tasks"


def build_todos_text(todos: list[dict]) -> Text:
    """构建待办事项列表的 Rich Text（不含摘要行）。

    供 ToolBlock 参数区域和 #todos-bar 面板共用。

    Args:
        todos: 任务列表，每项包含 content 和 status 字段

    Returns:
        Rich Text 对象，逐项列出任务
    """
    result = Text()

    for i, todo in enumerate(todos):
        content = todo.get("content", "")
        status = todo.get("status", "pending")
        icon = _STATUS_ICONS.get(status, "□")
        role = _STATUS_COLOR_ROLES.get(status, "text_muted")
        color = get_color(role)

        result.append(f"  {icon} ", style=color)
        if status == "completed":
            result.append(content, style=f"strike {get_color('text_muted')}")
        else:
            result.append(content, style=color if status == "in_progress" else "")
        if i < len(todos) - 1:
            result.append("\n")

    return result

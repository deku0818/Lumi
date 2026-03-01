"""任务列表工具（todos）渲染器

标题格式: todos（无关键参数）
参数区域: 以待办事项列表形式展示，顶部显示状态摘要
         pending 用 ○、in_progress 用 ◉、completed 用 ✓
输出区域: 同参数展示
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.theme import get_color

# 各状态对应的图标
_STATUS_ICONS: dict[str, str] = {
    "pending": "○",
    "in_progress": "◉",
    "completed": "✓",
}

# 各状态的中文标签
_STATUS_LABELS: dict[str, str] = {
    "pending": "待处理",
    "in_progress": "进行中",
    "completed": "已完成",
}

# 各状态对应的语义颜色角色
_STATUS_COLOR_ROLES: dict[str, str] = {
    "pending": "text_muted",
    "in_progress": "accent",
    "completed": "success",
}


class TodosRenderer:
    """todos 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，todos 无关键参数"""
        return "todos"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以待办事项列表形式展示任务，顶部显示状态摘要"""
        todos: list[dict] = args.get("todos", [])
        if not todos:
            return Static("", markup=False)
        return Static(_build_todos_text(todos))

    def render_output(self, output: str) -> Widget:
        """输出区域：简单文本展示"""
        if not output:
            return Static("", markup=False)
        return Static(output, markup=False)


def _build_todos_text(todos: list[dict]) -> Text:
    """构建待办事项列表的 Rich Text

    Args:
        todos: 任务列表，每项包含 content 和 status 字段

    Returns:
        包含状态摘要和任务列表的 Rich Text 对象
    """
    result = Text()

    # 统计各状态数量
    counts: dict[str, int] = {"pending": 0, "in_progress": 0, "completed": 0}
    for todo in todos:
        status = todo.get("status", "pending")
        if status in counts:
            counts[status] += 1

    # 摘要行
    summary_parts: list[str] = []
    for status_key in ("pending", "in_progress", "completed"):
        label = _STATUS_LABELS[status_key]
        count = counts[status_key]
        summary_parts.append(f"{label} {count}")
    result.append(
        " / ".join(summary_parts) + "\n",
        style=f"italic {get_color('text_muted')}",
    )
    result.append("\n")

    # 逐项展示
    for todo in todos:
        content = todo.get("content", "")
        status = todo.get("status", "pending")
        icon = _STATUS_ICONS.get(status, "○")
        role = _STATUS_COLOR_ROLES.get(status, "text_muted")
        result.append(f"  {icon} ", style=get_color(role))
        result.append(f"{content}\n")

    return result

"""Ask 工具渲染器

标题格式: ask(问题摘要)
摘要格式: 用户回答摘要
参数区域: 无（交互由 AskDialog 处理）
输出区域: 无（摘要行已包含回答信息）
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import truncate_for_title


@register_renderer("ask")
class AskRenderer(BaseRenderer):
    """ask 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: ask(问题摘要)"""
        questions = args.get("questions", [])
        count = len(questions)
        if count <= 1:
            q_text = (
                questions[0].get("question", "A question")
                if questions
                else "A question"
            )
            return f"ask({truncate_for_title(q_text)})"
        return f"ask({count} questions)"

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：用户回答摘要"""
        if is_error:
            return "Cancelled"
        if not output:
            return "Done"
        # 截断过长的回答
        display = output if len(output) <= 200 else output[:200] + "..."
        return display

    def render_output(self, output: str) -> Widget:
        """ask 不展示详细内容，摘要行已包含回答信息。"""
        return Static("", markup=False)

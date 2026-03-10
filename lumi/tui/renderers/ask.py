"""Ask 工具渲染器

标题格式: ask(问题摘要)
参数区域: 无（交互由 AskDialog 处理）
输出区域: 用户回答摘要
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import truncate_for_title


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

    def render_output(self, output: str) -> Widget:
        """展示用户回答摘要"""
        if not output:
            return Static("", markup=False)
        display = output if len(output) <= 500 else output[:500] + "..."
        return Static(display, markup=False)

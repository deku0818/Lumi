"""任务委托工具（agent）渲染器

标题格式: agent(代理名称)
参数区域: 原始 prompt 文本
输出区域: 原始模型输出
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer


@register_renderer("agent")
class AgentRenderer(BaseRenderer):
    """agent 工具渲染器"""

    title_arg_key = "name"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """直接展示 prompt 原文。"""
        prompt = args.get("prompt", "")
        if not prompt:
            return Static("", markup=False)
        return Static(prompt, markup=False)

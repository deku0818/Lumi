"""工具渲染器协议定义"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from textual.widget import Widget


@runtime_checkable
class ToolRenderer(Protocol):
    """工具渲染器协议

    每种工具可实现此协议，提供专属的标题、参数和输出渲染逻辑。
    """

    def render_title(self, name: str, args: dict) -> str:
        """生成标题文本，格式: 工具名(关键参数值)"""
        ...

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """生成参数展示区域的 Widget

        Args:
            args: 工具参数字典
            approval_mode: 审批模式下跳过内容折叠，展示完整内容
        """
        ...

    def render_output(self, output: str) -> Widget:
        """生成输出展示区域的 Widget"""
        ...

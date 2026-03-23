"""工具渲染器协议定义"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from textual.widget import Widget


@runtime_checkable
class ToolRenderer(Protocol):
    """工具渲染器协议

    每种工具可实现此协议，提供专属的标题、参数、摘要和输出渲染逻辑。

    渲染层次（工具完成后展开态）：
      ● tool_name(title_args)          ← render_title
        ⎿ 摘要文本                      ← render_summary（始终可见）
          详细内容...                    ← render_output（可选详情层）
    """

    def render_title(self, name: str, args: dict) -> str:
        """生成标题文本，格式: 工具名(关键参数值)"""
        ...

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """生成参数展示区域的 Widget（审批模式下使用）

        Args:
            args: 工具参数字典
            approval_mode: 审批模式下跳过内容折叠，展示完整内容
        """
        ...

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成 ⎿ 后面的摘要文本（工具完成时调用）。

        摘要行是展开态的第一行，始终可见。接收 args 和 output 是因为
        摘要内容可能来自任一侧（如 read 从 output 算行数，edit 从 args 算 diff）。

        Args:
            args: 工具参数字典
            output: 工具输出文本
            is_error: 是否为错误状态

        Returns:
            摘要文本（不含 ⎿ 前缀，由 ToolBlock 统一拼接）
        """
        ...

    def render_output(self, output: str) -> Widget:
        """生成输出展示区域的 Widget（摘要行下方的详情层）"""
        ...

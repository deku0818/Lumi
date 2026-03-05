"""Bash 工具渲染器

标题格式: bash(命令)
参数区域: 命令文本
输出区域: 终端风格（等宽字体、深色背景）展示命令输出，失败时红色高亮错误信息
         超过 30 行输出时在折叠摘要中显示行数
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color

# 折叠摘要的输出行数阈值
_OUTPUT_LINE_THRESHOLD = 30

# 用于检测错误输出的关键词
_ERROR_KEYWORDS = frozenset(
    {"error", "fail", "traceback", "exception", "timed out", "timeout"}
)


class BashRenderer:
    """bash 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: bash(命令)"""
        return f"bash({get_arg(args, 'command')})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """展示将要执行的命令文本"""
        command = args.get("command", "")
        if not command:
            return Static("", markup=False)
        # 以终端风格展示命令
        cmd_text = Text()
        cmd_text.append("$ ", style=f"bold {get_color('success')}")
        cmd_text.append(command, style="bold")
        return Static(cmd_text)

    def render_output(self, output: str) -> Widget:
        """以终端风格展示命令输出

        - 等宽字体、深色背景风格
        - 失败时红色高亮错误信息
        - 超过 30 行时显示行数提示
        """
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        line_count = len(lines)

        # 超过阈值时显示行数提示
        if line_count > _OUTPUT_LINE_THRESHOLD:
            summary = Text(
                f"🖥️ {line_count} 行输出",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        # 检测是否包含错误信息
        lower = output.lower()
        is_error = any(kw in lower for kw in _ERROR_KEYWORDS)

        if is_error:
            # 错误输出：红色高亮
            return Static(Text(output, style=get_color("error")))

        # 正常输出：终端风格（等宽字体）
        return Static(Text(output, style=get_color("text_muted")))

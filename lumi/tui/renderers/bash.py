"""Bash 工具渲染器

标题格式: bash(命令)
摘要格式: Ran successfully / Exited with error
参数区域: 命令文本
输出区域: 终端风格展示命令输出
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import get_arg, make_summary_static, truncate_for_title
from lumi.tui.theme import get_color

# 折叠摘要的输出行数阈值
_OUTPUT_LINE_THRESHOLD = 30

# 用于检测错误输出的关键词
_ERROR_KEYWORDS = frozenset(
    {"error", "fail", "traceback", "exception", "timed out", "timeout"}
)


@register_renderer("bash")
class BashRenderer(BaseRenderer):
    """bash 工具渲染器"""

    group_verb = "Ran"
    group_verb_active = "Running"
    group_noun = "command"

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: bash(命令)，多行/超长命令截断显示"""
        raw = get_arg(args, "command")
        return f"bash({truncate_for_title(raw)})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """展示将要执行的命令文本"""
        command = args.get("command", "")
        if not command:
            return Static("", markup=False)
        cmd_text = Text()
        cmd_text.append("$ ", style=f"bold {get_color('success')}")
        cmd_text.append(command, style="bold")
        return Static(cmd_text)

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Ran successfully / Exited with error"""
        if is_error:
            return "Exited with error"
        if output:
            lower = output.lower()
            if any(kw in lower for kw in _ERROR_KEYWORDS):
                return "Exited with error"
        return "Ran successfully"

    def render_output(self, output: str) -> Widget:
        """以终端风格展示命令输出。

        失败时红色高亮错误信息，超过 30 行时显示行数提示。
        """
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        line_count = len(lines)

        if line_count > _OUTPUT_LINE_THRESHOLD:
            return make_summary_static(f"🖥️ {line_count} 行输出")

        lower = output.lower()
        is_error = any(kw in lower for kw in _ERROR_KEYWORDS)

        if is_error:
            return Static(Text(output, style=get_color("error")))

        return Static(Text(output, style=get_color("text_muted")))

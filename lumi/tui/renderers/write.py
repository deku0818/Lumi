"""文件写入工具（write）渲染器

标题格式: write(文件路径)
摘要格式: Wrote N lines
参数区域: 语法高亮代码块展示文件内容（审批模式下使用）
输出区域: 写入成功/失败状态
"""

from __future__ import annotations

from rich.syntax import Syntax
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import (
    guess_lexer,
    make_summary_static,
    render_status_output,
)

# 折叠摘要的行数阈值
_LINE_THRESHOLD = 50


def _is_dark_theme() -> bool:
    """检测当前是否为暗色主题。"""
    try:
        from textual import active_app

        app = active_app.get()
        return getattr(app, "theme", "lumi-dark") == "lumi-dark"
    except (LookupError, Exception):
        return True


@register_renderer("write")
class WriteRenderer(BaseRenderer):
    """write 工具渲染器"""

    title_arg_key = "file_path"
    group_verb = "Wrote"
    group_verb_active = "Writing"
    group_noun = "file"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以语法高亮代码块展示将要写入的文件内容。

        超过 50 行时显示行数提示（审批模式下展示完整内容）。
        """
        content = args.get("content", "")
        path = args.get("file_path", "")

        if not content:
            return Static("", markup=False)

        line_count = content.count("\n") + (1 if not content.endswith("\n") else 0)

        if not approval_mode and line_count > _LINE_THRESHOLD:
            return make_summary_static(f"📄 {line_count} 行内容")

        lexer = guess_lexer(path)
        syntax = Syntax(
            content,
            lexer,
            theme="monokai" if _is_dark_theme() else "default",
            line_numbers=True,
            word_wrap=True,
        )
        return Static(syntax)

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Wrote N lines"""
        if is_error:
            return "Error"
        content = args.get("content", "")
        if not content:
            return "Done"
        line_count = content.count("\n") + (1 if not content.endswith("\n") else 0)
        return f"Wrote {line_count} lines"

    def render_output(self, output: str) -> Widget:
        """显示写入成功/失败状态"""
        return render_status_output(output)

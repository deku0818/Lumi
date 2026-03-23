"""文件编辑工具（edit）渲染器

标题格式: edit(文件路径)
摘要格式: Added X lines, removed Y lines
参数区域: 带行号的 Diff 视图（审批模式下使用）
输出区域: 带行号的 Diff 视图（展开时作为详情层）
"""

from __future__ import annotations

import difflib

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import render_status_output
from lumi.tui.theme import get_color

# 折叠摘要的 diff 行数阈值
_DIFF_LINE_THRESHOLD = 30

# diff 背景色（暗色/亮色主题各一套）
_DARK_STYLE_DEL = "on #351015"
_DARK_STYLE_ADD = "on #1a3520"
_LIGHT_STYLE_DEL = "on #fdd"
_LIGHT_STYLE_ADD = "on #dfd"


def _diff_styles() -> tuple[str, str]:
    """根据当前主题返回 (删除行样式, 新增行样式)。"""
    try:
        from textual import active_app

        app = active_app.get()
        is_dark = getattr(app, "theme", "lumi-dark") == "lumi-dark"
    except (LookupError, Exception):
        is_dark = True
    if is_dark:
        return _DARK_STYLE_DEL, _DARK_STYLE_ADD
    return _LIGHT_STYLE_DEL, _LIGHT_STYLE_ADD


@register_renderer("edit")
class EditRenderer(BaseRenderer):
    """edit 工具渲染器"""

    title_arg_key = "file_path"
    group_verb = "Edited"
    group_verb_active = "Editing"
    group_noun = "file"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以带行号的 Diff 视图展示 old_text 和 new_text 之间的差异。

        删除行红色背景，新增行绿色背景。
        超过 30 行差异时显示变更行数摘要（审批模式下跳过折叠）。
        参数缺少 old_text 或 new_text 时回退到 DefaultRenderer。
        """
        old_text = args.get("old_string")
        new_text = args.get("new_string")

        if old_text is None or new_text is None:
            from lumi.tui.renderers.default import DefaultRenderer

            return DefaultRenderer().render_args(args)

        diff_lines = _parse_diff(str(old_text), str(new_text))

        if not approval_mode and len(diff_lines) > _DIFF_LINE_THRESHOLD:
            added = sum(1 for kind, _, _, _ in diff_lines if kind == "+")
            removed = sum(1 for kind, _, _, _ in diff_lines if kind == "-")
            summary = Text(
                f"📝 {len(diff_lines)} 行差异（+{added} / -{removed}）",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        return Static(_render_diff_text(diff_lines))

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Added X lines, removed Y lines"""
        if is_error:
            return "Error"

        old_text = args.get("old_string", "")
        new_text = args.get("new_string", "")
        if old_text is None or new_text is None:
            return "Done"

        diff_lines = _parse_diff(str(old_text or ""), str(new_text or ""))
        added = sum(1 for kind, _, _, _ in diff_lines if kind == "+")
        removed = sum(1 for kind, _, _, _ in diff_lines if kind == "-")

        parts: list[str] = []
        if added:
            parts.append(f"Added {added} lines")
        if removed:
            parts.append(f"removed {removed} lines")
        return ", ".join(parts) if parts else "No changes"

    def render_output(self, output: str) -> Widget:
        """显示编辑成功/失败状态"""
        return render_status_output(output)


def _parse_diff(
    old_text: str, new_text: str
) -> list[tuple[str, int | None, int | None, str]]:
    """解析 unified diff，返回结构化行列表。

    每个元素: (kind, old_lineno, new_lineno, content)
    kind: " " 上下文行, "-" 删除行, "+" 新增行, "@" hunk 头
    """
    diff_iter = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        lineterm="",
    )

    result: list[tuple[str, int | None, int | None, str]] = []
    old_no = 0
    new_no = 0

    for line in diff_iter:
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("@@"):
            parts = line.split()
            try:
                old_part = parts[1]
                new_part = parts[2]
                old_no = int(old_part.split(",")[0].lstrip("-")) - 1
                new_no = int(new_part.split(",")[0].lstrip("+")) - 1
            except (IndexError, ValueError):
                pass
            result.append(("@", None, None, line.rstrip("\n")))
        elif line.startswith("-"):
            old_no += 1
            result.append(("-", old_no, None, line[1:].rstrip("\n")))
        elif line.startswith("+"):
            new_no += 1
            result.append(("+", None, new_no, line[1:].rstrip("\n")))
        else:
            old_no += 1
            new_no += 1
            result.append((" ", old_no, new_no, line[1:].rstrip("\n") if line else ""))

    return result


def _render_diff_text(
    diff_lines: list[tuple[str, int | None, int | None, str]],
) -> Text:
    """将结构化 diff 行渲染为带行号、红绿背景色块的 Rich Text。"""
    style_del, style_add = _diff_styles()

    max_no = 0
    for _, old_no, new_no, _ in diff_lines:
        if old_no and old_no > max_no:
            max_no = old_no
        if new_no and new_no > max_no:
            max_no = new_no
    width = max(len(str(max_no)), 2) if max_no else 2

    hunk_style = get_color("text_muted")
    ctx_style = get_color("border_separator")
    del_no_style = get_color("error")
    add_no_style = get_color("success")

    result = Text()
    for kind, old_no, new_no, content in diff_lines:
        if kind == "@":
            result.append(f"  {content}\n", style=hunk_style)
        elif kind == "-":
            no_str = str(old_no).rjust(width) if old_no else " " * width
            result.append(f"{no_str} ", style=del_no_style)
            result.append(f"-{content}\n", style=style_del)
        elif kind == "+":
            no_str = str(new_no).rjust(width) if new_no else " " * width
            result.append(f"{no_str} ", style=add_no_style)
            result.append(f"+{content}\n", style=style_add)
        else:
            no_str = str(old_no).rjust(width) if old_no else " " * width
            result.append(f"{no_str}  {content}\n", style=ctx_style)

    return result

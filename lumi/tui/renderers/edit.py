"""文件编辑工具（edit）渲染器

标题格式: edit(文件路径)
参数区域: 带行号的 Diff 视图，删除行红色背景、新增行绿色背景
         超过 30 行差异时显示变更行数摘要
         参数缺少 old_text 或 new_text 时回退到 DefaultRenderer
输出区域: 编辑成功/失败状态
"""

from __future__ import annotations

import difflib

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg, render_status_output
from lumi.tui.theme import get_color

# 折叠摘要的 diff 行数阈值
_DIFF_LINE_THRESHOLD = 30

# 样式常量（背景色保持不变，不属于语义颜色角色）
_STYLE_DEL = "on #3d1114"  # 红色背景 - 删除行
_STYLE_ADD = "on #1a3d1a"  # 绿色背景 - 新增行


def _style_hunk() -> str:
    """hunk 头样式"""
    return get_color("text_muted")


def _style_line_no() -> str:
    """上下文行号颜色"""
    return get_color("border_separator")


def _style_del_line_no() -> str:
    """删除行行号颜色"""
    return get_color("error")


def _style_add_line_no() -> str:
    """新增行行号颜色"""
    return get_color("success")


class EditRenderer:
    """edit 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: edit(文件路径)"""
        return f"edit({get_arg(args, 'path')})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以带行号的 Diff 视图展示 old_text 和 new_text 之间的差异

        - 删除行红色背景，新增行绿色背景
        - 超过 30 行差异时显示变更行数摘要（审批模式下跳过折叠）
        - 参数缺少 old_text 或 new_text 时回退到 DefaultRenderer
        """
        old_text = args.get("old_text")
        new_text = args.get("new_text")

        # 参数缺失时回退到 DefaultRenderer
        if old_text is None or new_text is None:
            from lumi.tui.renderers.default import DefaultRenderer

            return DefaultRenderer().render_args(args)

        diff_lines = _parse_diff(str(old_text), str(new_text))

        # 超过阈值时显示变更行数摘要（审批模式下展示完整差异）
        if not approval_mode and len(diff_lines) > _DIFF_LINE_THRESHOLD:
            added = sum(1 for kind, _, _, _ in diff_lines if kind == "+")
            removed = sum(1 for kind, _, _, _ in diff_lines if kind == "-")
            summary = Text(
                f"📝 {len(diff_lines)} 行差异（+{added} / -{removed}）",
                style=f"italic {get_color('text_muted')}",
            )
            return Static(summary)

        return Static(_render_diff_text(diff_lines))

    def render_output(self, output: str) -> Widget:
        """显示编辑成功/失败状态"""
        return render_status_output(output)


def _parse_diff(
    old_text: str, new_text: str
) -> list[tuple[str, int | None, int | None, str]]:
    """解析 unified diff，返回结构化行列表

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
        # 跳过 --- / +++ 文件头
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("@@"):
            # 解析 hunk 头: @@ -old_start,old_count +new_start,new_count @@
            parts = line.split()
            try:
                old_part = parts[1]  # -old_start,old_count
                new_part = parts[2]  # +new_start,new_count
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
    """将结构化 diff 行渲染为带行号、红绿背景色块的 Rich Text

    格式参考:
      5 -This script converts images to ...
      5 +该脚本将图像转换为 ...
    """
    # 计算行号宽度
    max_no = 0
    for _, old_no, new_no, _ in diff_lines:
        if old_no and old_no > max_no:
            max_no = old_no
        if new_no and new_no > max_no:
            max_no = new_no
    width = max(len(str(max_no)), 2) if max_no else 2

    result = Text()
    for kind, old_no, new_no, content in diff_lines:
        if kind == "@":
            # hunk 头
            result.append(f"  {content}\n", style=_style_hunk())
        elif kind == "-":
            # 删除行: 行号(红) + 空格 + -内容(红色背景)
            no_str = str(old_no).rjust(width) if old_no else " " * width
            result.append(f"{no_str} ", style=_style_del_line_no())
            result.append(f"-{content}\n", style=_STYLE_DEL)
        elif kind == "+":
            # 新增行: 行号(绿) + 空格 + +内容(绿色背景)
            no_str = str(new_no).rjust(width) if new_no else " " * width
            result.append(f"{no_str} ", style=_style_add_line_no())
            result.append(f"+{content}\n", style=_STYLE_ADD)
        else:
            # 上下文行
            no_str = str(old_no).rjust(width) if old_no else " " * width
            result.append(f"{no_str}  {content}\n", style=_style_line_no())

    return result

"""文件读取工具（read）渲染器

标题格式: read(文件路径)
摘要格式: Read N lines 或 Read N lines · lines X-Y
输出区域: 无（read 只展示摘要，不展示文件内容）
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers._core import register_renderer
from lumi.tui.renderers.base import BaseRenderer


@register_renderer("read")
class ReadRenderer(BaseRenderer):
    """read 工具渲染器"""

    title_arg_key = "file_path"
    group_verb = "Read"
    group_verb_active = "Reading"
    group_noun = "file"

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成摘要：Read N lines · lines X-Y"""
        if is_error:
            return "Error"

        line_count = len(output.splitlines()) if output else 0
        offset = args.get("offset", 0)

        # 有 offset 时显示行范围
        if offset:
            end = offset + line_count
            return f"Read {line_count} lines · lines {offset + 1}-{end}"

        return f"Read {line_count} lines"

    def render_output(self, output: str) -> Widget:
        """read 不展示详细内容，摘要行已包含全部信息。"""
        return Static("", markup=False)

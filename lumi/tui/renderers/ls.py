"""目录列表工具（ls）渲染器

标题格式: ls(目录路径)
参数区域: 无（路径已在标题中展示）
输出区域: 以列表形式展示目录内容，目录使用 📁 图标，文件使用 📄 图标
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color


class LsRenderer:
    """ls 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: ls(目录路径)"""
        return f"ls({get_arg(args, 'path')})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """ls 参数简单（仅 path），路径已在标题中展示，无需额外渲染"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """以列表形式展示目录内容，目录和文件使用不同图标区分

        解析 ls 工具输出，将 [目录] 前缀替换为 📁，[文件] 前缀替换为 📄。
        """
        if not output:
            return Static("", markup=False)

        result = Text()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("[目录]"):
                # 目录项：📁 图标 + 路径
                content = stripped.removeprefix("[目录]").strip()
                result.append("📁 ", style=f"bold {get_color('accent')}")
                result.append(content + "\n", style=get_color("success"))
            elif stripped.startswith("[文件]"):
                # 文件项：📄 图标 + 路径和元信息
                content = stripped.removeprefix("[文件]").strip()
                result.append("📄 ", style=f"bold {get_color('info')}")
                result.append(content + "\n", style=get_color("text_muted"))
            else:
                # 未知格式，原样展示
                result.append(stripped + "\n")

        return Static(result)

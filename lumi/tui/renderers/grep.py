"""内容搜索工具（grep）渲染器

标题格式: grep(搜索模式)
参数区域: 无（模式已在标题中展示）
输出区域: 以分组形式展示匹配结果，匹配关键词高亮显示
"""

from __future__ import annotations

import re

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color


class GrepRenderer(BaseRenderer):
    """grep 工具渲染器"""

    def __init__(self) -> None:
        self._pattern: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: grep(搜索模式)，同时缓存 pattern 供 render_output 使用。"""
        self._pattern = get_arg(args, "pattern")
        return f"grep({self._pattern})"

    def render_output(self, output: str) -> Widget:
        """以分组形式展示匹配结果，关键词高亮。"""
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        if not lines:
            return Static("", markup=False)

        result = Text()

        header = lines[0].strip()
        if header:
            result.append(f"🔍 {header}\n\n", style=f"bold {get_color('accent')}")

        groups: dict[str, list[tuple[str, str]]] = {}
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("..."):
                result.append(
                    f"\n{stripped}\n",
                    style=f"italic {get_color('text_muted')}",
                )
                continue
            file_path, line_no, content = _parse_match_line(stripped)
            if file_path:
                groups.setdefault(file_path, []).append((line_no, content))
            else:
                result.append(stripped + "\n")

        for file_path, matches in groups.items():
            result.append(f"📄 {file_path}\n", style=f"bold {get_color('info')}")
            for line_no, content in matches:
                result.append(f"  {line_no}: ", style=get_color("text_muted"))
                _append_highlighted(result, content, self._pattern)
                result.append("\n")
            result.append("\n")

        return Static(result)


def _parse_match_line(line: str) -> tuple[str, str, str]:
    """解析单行匹配结果，格式: path:line_no: content。

    Returns:
        (文件路径, 行号, 匹配内容)，解析失败时返回 ("", "", "")
    """
    match = re.match(r"^(.+?):(\d+):\s?(.*)", line)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return "", "", ""


def _append_highlighted(text: Text, content: str, pattern: str) -> None:
    """将内容追加到 Text 对象，匹配 pattern 的部分高亮显示。"""
    if not pattern:
        text.append(content)
        return

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        text.append(content)
        return

    last_end = 0
    for m in regex.finditer(content):
        if m.start() > last_end:
            text.append(content[last_end : m.start()])
        text.append(m.group(), style=f"bold {get_color('accent')} on #3e2723")
        last_end = m.end()

    if last_end < len(content):
        text.append(content[last_end:])

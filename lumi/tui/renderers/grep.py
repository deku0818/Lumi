"""内容搜索工具（grep）渲染器

标题格式: grep(搜索模式)
参数区域: 无（模式已在标题中展示）
输出区域: 以分组形式展示匹配结果，每个匹配项显示文件路径、行号和匹配行内容，
         匹配关键词高亮显示
"""

from __future__ import annotations

import re

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color


class GrepRenderer:
    """grep 工具渲染器"""

    def __init__(self) -> None:
        self._pattern: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: grep(搜索模式)"""
        self._pattern = get_arg(args, "pattern")
        return f"grep({self._pattern})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """grep 参数简单（pattern + path + file_glob），模式已在标题中展示，无需额外渲染"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """以分组形式展示匹配结果，关键词高亮

        解析 grep 工具输出格式:
          找到 N 处匹配:
            path/to/file.py:42: matched content
            path/to/file.py:55: another match
        按文件路径分组展示，匹配关键词高亮。
        """
        if not output:
            return Static("", markup=False)

        lines = output.splitlines()
        if not lines:
            return Static("", markup=False)

        result = Text()

        # 第一行通常是摘要（如 "找到 N 处匹配:"）或错误信息
        header = lines[0].strip()
        if header:
            result.append(f"🔍 {header}\n\n", style=f"bold {get_color('accent')}")

        # 解析匹配行，按文件路径分组
        groups: dict[str, list[tuple[str, str]]] = {}
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            # 截断提示行（如 "... 还有 N 处匹配未显示"）
            if stripped.startswith("..."):
                result.append(
                    f"\n{stripped}\n",
                    style=f"italic {get_color('text_muted')}",
                )
                continue
            # 解析 "path:line_no: content" 格式
            file_path, line_no, content = _parse_match_line(stripped)
            if file_path:
                groups.setdefault(file_path, []).append((line_no, content))
            else:
                # 无法解析的行原样展示
                result.append(stripped + "\n")

        # 按文件分组展示
        for file_path, matches in groups.items():
            result.append(f"📄 {file_path}\n", style=f"bold {get_color('info')}")
            for line_no, content in matches:
                result.append(f"  {line_no}: ", style=get_color("text_muted"))
                _append_highlighted(result, content, self._pattern)
                result.append("\n")
            result.append("\n")

        return Static(result)


def _parse_match_line(line: str) -> tuple[str, str, str]:
    """解析单行匹配结果，格式: path:line_no: content

    Returns:
        (文件路径, 行号, 匹配内容)，解析失败时返回 ("", "", "")
    """
    # 匹配 "path:数字: 内容" 格式
    match = re.match(r"^(.+?):(\d+):\s?(.*)", line)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return "", "", ""


def _append_highlighted(text: Text, content: str, pattern: str) -> None:
    """将内容追加到 Text 对象，匹配 pattern 的部分高亮显示

    Args:
        text: 目标 Rich Text 对象
        content: 待追加的文本内容
        pattern: 搜索模式（正则表达式）
    """
    if not pattern:
        text.append(content)
        return

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        # 正则无效时原样展示
        text.append(content)
        return

    last_end = 0
    for m in regex.finditer(content):
        # 追加匹配前的普通文本
        if m.start() > last_end:
            text.append(content[last_end : m.start()])
        # 追加高亮的匹配文本
        text.append(m.group(), style=f"bold {get_color('accent')} on #3e2723")
        last_end = m.end()

    # 追加剩余文本
    if last_end < len(content):
        text.append(content[last_end:])

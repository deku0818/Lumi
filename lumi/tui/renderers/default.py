"""默认工具渲染器 - 格式化键值对展示，用于 MCP 工具和未注册工具的兜底"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

# 单个参数值的最大显示长度
_MAX_VALUE_LEN = 200


class DefaultRenderer:
    """默认渲染器

    标题格式: 工具名(首个参数值)
    参数展示: 格式化键值对，每个参数独占一行，长文本截断
    输出展示: 格式化纯文本
    """

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: 工具名(首个参数值)"""
        first_value = _first_arg_value(args)
        return f"{name}({first_value})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """以键值对形式展示参数，跳过首个参数（已在标题中展示），长文本截断"""
        if not args:
            return Static("", markup=False)
        lines: list[str] = []
        for i, (key, value) in enumerate(args.items()):
            if i == 0:
                continue
            display_value = _truncate(str(value), _MAX_VALUE_LEN)
            lines.append(f"{key}: {display_value}")
        return Static("\n".join(lines), markup=False)

    def render_output(self, output: str) -> Widget:
        """以纯文本展示输出"""
        if not output:
            return Static("", markup=False)
        return Static(output, markup=False)


def _first_arg_value(args: dict) -> str:
    """提取参数字典中首个参数的值，缺失时返回空字符串"""
    if not args:
        return ""
    first = next(iter(args.values()))
    text = str(first)
    return _truncate(text, 60)


def _truncate(text: str, max_len: int) -> str:
    """截断文本，超出长度时添加省略号"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."

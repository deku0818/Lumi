"""默认工具渲染器 - 格式化键值对展示，用于 MCP 工具和未注册工具的兜底"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.base import BaseRenderer
from lumi.tui.renderers.utils import truncate_for_title

# 单个参数值的最大显示长度
_MAX_VALUE_LEN = 200


class DefaultRenderer(BaseRenderer):
    """默认渲染器，继承 BaseRenderer。

    标题格式: 工具名(首个参数值)
    参数展示: 格式化键值对，每个参数独占一行，长文本截断
    输出展示: 继承 BaseRenderer 的纯文本实现
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
            display_value = truncate_for_title(str(value), max_len=_MAX_VALUE_LEN)
            lines.append(f"{key}: {display_value}")
        return Static("\n".join(lines), markup=False)


def _first_arg_value(args: dict) -> str:
    """提取参数字典中首个参数的值，缺失时返回空字符串"""
    if not args:
        return ""
    first = next(iter(args.values()))
    text = str(first)
    return truncate_for_title(text)

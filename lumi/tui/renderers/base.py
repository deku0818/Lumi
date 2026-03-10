"""渲染器基类 - 提供常见默认实现，减少各渲染器的样板代码

子类只需覆盖 ``title_arg_key`` 和有特殊逻辑的方法即可。
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg, truncate_for_title

# 单个参数值的最大显示长度
_MAX_VALUE_LEN = 200


class BaseRenderer:
    """渲染器基类

    标题格式: 工具名(title_arg_key 对应的参数值)
    参数展示: 默认为空（大多数工具参数已在标题中展示）
    输出展示: 纯文本
    """

    # 子类覆盖：标题中使用的参数 key，为空时标题不含参数值
    title_arg_key: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: 工具名(参数值)"""
        if self.title_arg_key:
            value = get_arg(args, self.title_arg_key)
            return f"{name}({truncate_for_title(value)})"
        return name

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """默认参数展示：空（子类按需覆盖）"""
        return Static("", markup=False)

    def render_output(self, output: str) -> Widget:
        """默认输出展示：纯文本"""
        if not output:
            return Static("", markup=False)
        return Static(output, markup=False)

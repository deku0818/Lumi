"""渲染器基类 - 提供常见默认实现，减少各渲染器的样板代码

子类只需覆盖 ``title_arg_key`` 和有特殊逻辑的方法即可。

渲染层次（工具完成后展开态）：
  ● tool_name(title_args)          ← render_title
    ⎿ 摘要文本                      ← render_summary（始终可见）
      详细内容...                    ← render_output（可选详情层）
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
    摘要展示: 默认 "Done"（子类按需覆盖）
    输出展示: 纯文本
    """

    # 子类覆盖：标题中使用的参数 key，为空时标题不含参数值
    title_arg_key: str = ""

    # ── ToolGroup 合并摘要属性 ──
    # 子类覆盖以支持工具组合并显示
    group_verb: str = ""  # 完成态动词，如 "Read" / "Edited"
    group_verb_active: str = ""  # 进行态动词，如 "Reading" / "Editing"
    group_noun: str = ""  # 分组名词，如 "file" / "command"
    # 用于提取文件路径的参数 key（合并摘要中显示文件名），默认复用 title_arg_key
    group_target_key: str = ""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: 工具名(参数值)"""
        if self.title_arg_key:
            value = get_arg(args, self.title_arg_key)
            return f"{name}({truncate_for_title(value)})"
        return name

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """默认参数展示：空（子类按需覆盖）"""
        return Static("", markup=False)

    def render_summary(self, args: dict, output: str, *, is_error: bool = False) -> str:
        """生成 ⎿ 后面的摘要文本。

        默认实现：错误时返回 "Error"，成功时返回 "Done"。
        子类覆盖以提供更有意义的摘要（如行数、匹配数等）。

        Args:
            args: 工具参数字典
            output: 工具输出文本
            is_error: 是否为错误状态

        Returns:
            摘要文本（不含 ⎿ 前缀）
        """
        if is_error:
            return "Error"
        return "Done"

    def render_output(self, output: str) -> Widget:
        """默认输出展示：纯文本"""
        if not output:
            return Static("", markup=False)
        return Static(output, markup=False)

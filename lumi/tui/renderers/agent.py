"""任务委托工具（agent）渲染器

标题格式: agent(代理名称)
参数区域: 展示任务描述（prompt），长文本截断折叠
输出区域: 展示子代理返回的执行结果摘要
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.renderers.utils import get_arg
from lumi.tui.theme import get_color

# prompt 文本截断阈值（字符数）
_PROMPT_MAX_LEN = 500


class AgentRenderer:
    """agent 工具渲染器"""

    def render_title(self, name: str, args: dict) -> str:
        """生成标题，格式: agent(代理名称)"""
        return f"agent({get_arg(args, 'name')})"

    def render_args(self, args: dict, *, approval_mode: bool = False) -> Widget:
        """展示任务描述（prompt），长文本截断

        超过 500 字符时截断并添加省略号提示。
        """
        prompt = args.get("prompt", "")
        if not prompt:
            return Static("", markup=False)

        result = Text()
        result.append("📋 任务描述:\n", style=f"bold {get_color('accent')}")

        if len(prompt) > _PROMPT_MAX_LEN:
            result.append(prompt[:_PROMPT_MAX_LEN], style=get_color("text_muted"))
            result.append(
                f"\n... (共 {len(prompt)} 字符)",
                style=f"italic {get_color('text_muted')}",
            )
        else:
            result.append(prompt, style=get_color("text_muted"))

        return Static(result)

    def render_output(self, output: str) -> Widget:
        """展示子代理返回的执行结果摘要"""
        if not output:
            return Static("", markup=False)

        result = Text()
        result.append("📝 执行结果:\n", style=f"bold {get_color('success')}")
        result.append(output, style=get_color("text_muted"))
        return Static(result)

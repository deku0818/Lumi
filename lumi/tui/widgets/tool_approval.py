"""工具审批组件

支持权限引擎的动态选项（allow_once / always_allow_exact / always_allow_pattern / reject），
同时向后兼容无 options 字段的简单审批。
使用 Textual 原生 CSS border 实现自适应闭合边框。
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Rule, Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.renderers.utils import escape_markup
from lumi.tui.theme import get_color
from lumi.tui.widgets.approval_base import BaseApproval

logger = logging.getLogger(__name__)
_FALLBACK_RENDERER = DefaultRenderer()

# 默认选项（无权限引擎时的回退）
_DEFAULT_OPTIONS: tuple[dict[str, str], ...] = (
    {"key": "approve", "label": "允许本次执行"},
    {"key": "reject", "label": "拒绝"},
)

# 选项 key → 语义颜色角色映射
_OPTION_COLOR_ROLES: dict[str, str] = {
    "approve": "success",
    "allow_once": "success",
    "always_allow_exact": "accent",
    "always_allow_pattern": "accent",
    "reject": "error",
}


class ToolApproval(BaseApproval):
    """工具审批组件 - 键盘驱动的列表选择器

    使用 Textual 原生 border: round 实现自适应闭合边框，
    border_title 显示标题，border_subtitle 显示键盘提示。
    """

    class Decided(BaseApproval.Decided):
        """工具审批决定（独立类型，确保 Textual 消息路由正确）"""

    DEFAULT_CSS = """
    ToolApproval {
        margin: 0 0 0 2;
        padding: 0 1;
        background: transparent;
        height: auto;
        border: round $accent;
    }

    ToolApproval .approval-warning {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-options {
        height: auto;
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-line {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-tool-content {
        margin: 0 0 0 4;
        padding: 0;
        height: auto;
    }

    ToolApproval Rule {
        margin: 0;
        color: $accent;
    }

    ToolApproval _ScrollableContent {
        margin: 0;
        padding: 0;
        height: auto;
        max-height: 20;
        scrollbar-size: 1 1;
    }
    """

    def __init__(self, interrupt_data: dict) -> None:
        # 从 interrupt 数据构建选项列表
        raw_options = interrupt_data.get("options")
        if raw_options and isinstance(raw_options, list):
            options: tuple[dict[str, str], ...] = tuple(raw_options)
        else:
            options = _DEFAULT_OPTIONS

        super().__init__(
            options=options,
            option_color_roles=_OPTION_COLOR_ROLES,
            cancel_key="cancel",
            options_selector="#approval-options",
            content_selector="#tool-approval-content",
            classes="tool-approval",
        )
        self._data = interrupt_data

        # 构建 border_title：合并边界违规信息
        title_parts = ["⚠ 权限审批"]
        boundary_violations = interrupt_data.get("boundary_violations", [])
        if boundary_violations:
            violations_str = ", ".join(boundary_violations)
            title_parts.append(f"[@click=]⚠ 路径超出工作区边界: {violations_str}[/]")
        self.border_title = " ".join(title_parts)
        self.border_subtitle = "↑↓ 选择 · enter 确认 · esc 拒绝"

    def compose(self) -> ComposeResult:
        accent = get_color("accent")

        # 可滚动内容区域
        with _ScrollableContent(id="tool-approval-content"):
            # 渲染警告信息
            warnings = self._data.get("warnings", [])
            for warning in warnings:
                yield Static(
                    f"[bold red]{escape_markup(warning)}[/]",
                    classes="approval-warning",
                )

            # 工具列表
            tool_calls = self._data.get("tool_calls", [])
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                if not isinstance(args, dict):
                    args = {}

                renderer = get_renderer(name)
                try:
                    title_text = renderer.render_title(name, args)
                except Exception:
                    logger.warning(
                        "[ToolApproval] render_title 失败，回退到默认: %s",
                        name,
                        exc_info=True,
                    )
                    title_text = _FALLBACK_RENDERER.render_title(name, args)

                yield Static(
                    f"[{accent} bold]● {escape_markup(title_text)}[/]",
                    classes="approval-line",
                )

                # 渲染工具参数内容
                try:
                    args_widget = renderer.render_args(args, approval_mode=True)
                except Exception:
                    logger.warning(
                        "[ToolApproval] render_args 失败，回退到默认: %s",
                        name,
                        exc_info=True,
                    )
                    args_widget = _FALLBACK_RENDERER.render_args(args)
                yield _IndentedContent(args_widget)

        # 分隔线
        yield Rule()

        # 选项区域
        yield Static(
            self._render_options(),
            id="approval-options",
            classes="approval-options",
            markup=False,
        )

    def _render_options(self, max_label_len: int = 70):
        """渲染选项列表，长 label 截断显示。"""
        return super()._render_options(max_label_len=max_label_len)


class _ScrollableContent(VerticalScroll):
    """审批内容的可滚动容器"""


class _IndentedContent(Vertical):
    """为渲染器输出的 Widget 添加缩进的容器"""

    DEFAULT_CSS = """
    _IndentedContent {
        margin: 0 0 0 4;
        padding: 0;
        height: auto;
    }
    """

    def __init__(self, content_widget) -> None:
        super().__init__()
        self._content_widget = content_widget

    def compose(self) -> ComposeResult:
        yield self._content_widget

"""工具审批组件 - Unicode box-drawing 布局"""

from __future__ import annotations

import logging

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)
_FALLBACK_RENDERER = DefaultRenderer()

# 分隔线宽度
_SEP_WIDTH = 30


class ToolApproval(Vertical):
    """工具审批组件 - 键盘驱动的列表选择器

    使用 Unicode box-drawing 字符（│├└）构建视觉边界，
    将工具内容和操作选项清晰分隔。
    """

    can_focus = True

    _options: tuple[tuple[str, str], ...] = (
        ("approve", "允许本次执行"),
        ("auto", "允许本次会话内的所有工具调用"),
        ("reject", "拒绝"),
    )

    # 选项 key → 语义颜色角色映射
    _OPTION_COLOR_ROLES: dict[str, str] = {
        "approve": "success",
        "auto": "accent",
        "reject": "error",
    }

    DEFAULT_CSS = """
    ToolApproval {
        margin: 0 1 0 2;
        padding: 0;
        background: transparent;
        border: none;
        height: auto;
    }

    ToolApproval .approval-label {
        text-style: bold;
        margin: 0;
        padding: 0;
        color: $accent;
    }

    ToolApproval .tool-call-title {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-sep {
        margin: 0;
        padding: 0;
        color: $border;
    }

    ToolApproval .approval-options {
        height: auto;
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-hint {
        margin: 0;
        padding: 0;
        color: $border;
    }
    """

    class Decided(Message):
        """用户做出审批决定"""

        def __init__(self, decision: str) -> None:
            super().__init__()
            self.decision = decision  # "approve" | "auto" | "reject"

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(classes="tool-approval")
        self._data = interrupt_data
        self._selected = 0

    def compose(self) -> ComposeResult:
        msg = escape(self._data.get("message", "是否执行以下工具？"))
        accent = get_color("accent")
        border = get_color("border_separator")
        yield Static(f"[bold {accent}]⚠[/] {msg}", classes="approval-label")

        tool_calls = self._data.get("tool_calls", [])
        for i, tc in enumerate(tool_calls):
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

            # 工具标题行
            yield Static(
                f"  [bold {accent}]● {escape(title_text)}[/]",
                classes="tool-call-title",
            )
            # 竖线 + 工具内容
            yield Static(f"[{border}]  │[/]")
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

            # 工具之间用 ├─── 分隔
            if i < len(tool_calls) - 1:
                yield Static(
                    f"[{border}]  ├{'─' * _SEP_WIDTH}[/]", classes="approval-sep"
                )

        # 工具内容与选项之间的分隔线
        yield Static(f"[{border}]  ├{'─' * _SEP_WIDTH}[/]", classes="approval-sep")

        # 选项区域（带竖线前缀）
        yield Static(
            self._render_options(), id="approval-options", classes="approval-options"
        )

        # 提示行
        yield Static(
            f"[{border}]  │[/]  [dim](↑↓ 选择, enter 确认, esc 拒绝)[/dim]",
            classes="approval-hint",
        )

        # 底部收尾
        yield Static(f"[{border}]  └{'─' * _SEP_WIDTH}[/]", classes="approval-sep")

    def on_mount(self) -> None:
        """挂载后自动获取焦点"""
        self.focus()

    def on_key(self, event) -> None:
        """键盘事件处理"""
        if event.key == "up":
            self._selected = (self._selected - 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "down":
            self._selected = (self._selected + 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "enter":
            decision = self._options[self._selected][0]
            self.post_message(self.Decided(decision))
            self.call_later(self.remove)
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decided("reject"))
            self.call_later(self.remove)
            event.stop()

    def _render_options(self) -> str:
        """渲染选项列表，每行带竖线前缀"""
        border = get_color("border_separator")
        lines: list[str] = []
        for i, (key, label) in enumerate(self._options):
            color = get_color(self._OPTION_COLOR_ROLES[key])
            if i == self._selected:
                lines.append(f"[{border}]  │[/]  [bold {color}]● {label}[/]")
            else:
                lines.append(f"[{border}]  │[/]    {label}")
        return "\n".join(lines)

    def _refresh_options(self) -> None:
        """刷新选项显示"""
        self.query_one("#approval-options", Static).update(self._render_options())


class _IndentedContent(Vertical):
    """为渲染器输出的 Widget 添加竖线前缀的容器"""

    DEFAULT_CSS = """
    _IndentedContent {
        margin: 0 0 0 5;
        padding: 0;
        height: auto;
    }
    """

    def __init__(self, content_widget) -> None:
        super().__init__()
        self._content_widget = content_widget

    def compose(self) -> ComposeResult:
        yield self._content_widget

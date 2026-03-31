"""工具审批组件 - 圆角卡片布局

支持权限引擎的动态选项（allow_once / always_allow_exact / always_allow_pattern / reject），
同时向后兼容无 options 字段的简单审批。
"""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.renderers.utils import escape_markup, truncate_for_title
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)
_FALLBACK_RENDERER = DefaultRenderer()

# 分隔线宽度
_SEP_WIDTH = 46

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


class ToolApproval(Vertical):
    """工具审批组件 - 键盘驱动的列表选择器

    使用圆角卡片布局（╭│├╰），标题嵌入顶部边框，
    提示嵌入底部边框。

    支持两种模式：
    - 动态选项：从 interrupt 数据的 options 字段读取（权限引擎）
    - 默认选项：approve / reject（无权限引擎时回退）
    """

    can_focus = True

    DEFAULT_CSS = """
    ToolApproval {
        margin: 0 1 0 0;
        padding: 0 1;
        background: transparent;
        border: none;
        height: auto;
    }

    ToolApproval .approval-border {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-line {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-options {
        height: auto;
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-warning {
        margin: 0;
        padding: 0;
    }

    ToolApproval .approval-tool-content {
        margin: 0 0 0 6;
        padding: 0;
        height: auto;
    }

    ToolApproval _ScrollableContent {
        margin: 0;
        padding: 0;
        height: auto;
        max-height: 20;
        scrollbar-size: 1 1;
    }
    """

    class Decided(Message):
        """用户做出审批决定"""

        def __init__(self, decision: str) -> None:
            super().__init__()
            self.decision = decision

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(classes="tool-approval")
        self._data = interrupt_data
        self._selected = 0

        # 从 interrupt 数据构建选项列表
        raw_options = interrupt_data.get("options")
        if raw_options and isinstance(raw_options, list):
            self._options: tuple[dict[str, str], ...] = tuple(raw_options)
        else:
            self._options = _DEFAULT_OPTIONS

    def compose(self) -> ComposeResult:
        accent = get_color("accent")
        border = get_color("border_separator")

        # 顶部圆角 + 标题
        yield Static(
            f"[{border}]  ╭─[/] [{accent} bold]⚠ 权限审批[/] [{border}]{'─' * _SEP_WIDTH}[/]",
            classes="approval-border",
        )

        # 可滚动内容区域（shift+↑↓ / pgup/pgdn 滚动）
        with _ScrollableContent(id="tool-approval-content"):
            # 渲染警告信息
            warnings = self._data.get("warnings", [])
            for warning in warnings:
                yield Static(
                    f"[{border}]  │[/]   [bold red]{escape_markup(warning)}[/]",
                    classes="approval-warning",
                )

            # 渲染工作区边界违规
            boundary_violations = self._data.get("boundary_violations", [])
            for violation in boundary_violations:
                yield Static(
                    f"[{border}]  │[/]   [bold yellow]⚠ 路径超出工作区边界: {escape_markup(violation)}[/]",
                    classes="approval-warning",
                )

            # 空行
            yield Static(f"[{border}]  │[/]", classes="approval-line")

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
                    f"[{border}]  │[/]   [{accent} bold]● {escape_markup(title_text)}[/]",
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

            # 空行
            yield Static(f"[{border}]  │[/]", classes="approval-line")
        yield Static(
            f"[{border}]  ├{'─' * (_SEP_WIDTH + 10)}[/]",
            classes="approval-border",
        )

        # 选项区域
        yield Static(
            self._render_options(),
            id="approval-options",
            classes="approval-options",
            markup=False,
        )

        # 空行
        yield Static(f"[{border}]  │[/]", classes="approval-line")

        # 底部圆角 + 提示
        yield Static(
            f"[{border}]  ╰─[/] [dim]↑↓ 选择 · shift+↑↓ 滚动 · enter 确认 · esc 拒绝[/dim] [{border}]{'─' * (_SEP_WIDTH - 27)}[/]",
            classes="approval-border",
        )

    def on_mount(self) -> None:
        """挂载后自动获取焦点"""
        self.focus()

    def scroll_content(self, direction: str) -> None:
        """滚动内容区域，由 app 级快捷键委派调用。"""
        try:
            container = self.query_one("#tool-approval-content", VerticalScroll)
        except NoMatches:
            return
        if direction == "up":
            container.scroll_up(animate=False)
        elif direction == "down":
            container.scroll_down(animate=False)
        elif direction == "page_up":
            container.scroll_page_up(animate=False)
        elif direction == "page_down":
            container.scroll_page_down(animate=False)

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
            decision = self._options[self._selected]["key"]
            self.post_message(self.Decided(decision))
            self.call_later(self.remove)
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decided("cancel"))
            self.call_later(self.remove)
            event.stop()

    def _render_options(self) -> Text:
        """渲染选项列表，每行带竖线前缀，长 label 截断显示。"""
        border = get_color("border_separator")
        result = Text()
        for i, opt in enumerate(self._options):
            if i > 0:
                result.append("\n")
            key = opt["key"]
            label = opt.get("label", key)
            label = truncate_for_title(label, max_len=70)
            color = get_color(_OPTION_COLOR_ROLES.get(key, "foreground"))
            result.append("  │", style=border)
            if i == self._selected:
                result.append(f"   ❯ {label}", style=f"bold {color}")
            else:
                result.append(f"     {label}")
        return result

    def _refresh_options(self) -> None:
        """刷新选项显示"""
        self.query_one("#approval-options", Static).update(self._render_options())


class _ScrollableContent(VerticalScroll):
    """审批内容的可滚动容器"""


class _IndentedContent(Vertical):
    """为渲染器输出的 Widget 添加竖线前缀的容器"""

    DEFAULT_CSS = """
    _IndentedContent {
        margin: 0 0 0 6;
        padding: 0;
        height: auto;
    }
    """

    def __init__(self, content_widget) -> None:
        super().__init__()
        self._content_widget = content_widget

    def compose(self) -> ComposeResult:
        yield self._content_widget

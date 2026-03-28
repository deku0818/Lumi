"""计划审批组件 - 用户审批 Agent 提交的实施计划

Agent 在计划模式完成后调用 exit_plan_mode 工具触发此组件，
用户可以批准计划（开始实施）或拒绝（继续修改计划）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Markdown, Static

from lumi.tui.renderers.utils import escape_markup
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)

# 选项定义
_OPTIONS: tuple[dict[str, str], ...] = (
    {"key": "approved", "label": "批准 — 开始实施"},
    {"key": "rejected", "label": "拒绝 — 继续修改计划"},
)

# 选项 key → 语义颜色角色
_OPTION_COLOR_ROLES: dict[str, str] = {
    "approved": "success",
    "rejected": "error",
}

_SEP_WIDTH = 46


class PlanApproval(Vertical):
    """计划审批组件 - 键盘驱动的批准/拒绝选择器"""

    can_focus = True

    DEFAULT_CSS = """
    PlanApproval {
        margin: 0 1 0 0;
        padding: 0 1;
        background: transparent;
        border: none;
        height: auto;
    }

    PlanApproval .plan-border {
        margin: 0;
        padding: 0;
    }

    PlanApproval .plan-line {
        margin: 0;
        padding: 0;
    }

    PlanApproval .plan-options {
        height: auto;
        margin: 0;
        padding: 0;
    }

    PlanApproval .plan-content {
        margin: 0 0 0 6;
        padding: 0;
        height: auto;
        max-height: 30;
        overflow-y: auto;
    }
    """

    class Decided(Message):
        """用户做出计划审批决定"""

        def __init__(self, decision: str) -> None:
            super().__init__()
            self.decision = decision

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(classes="plan-approval")
        self._data = interrupt_data
        self._selected = 0

    def compose(self) -> ComposeResult:
        accent = get_color("accent")
        border = get_color("border_separator")
        plan_path = self._data.get("plan_file_path", "")

        # 顶部圆角 + 标题
        yield Static(
            f"[{border}]  ╭─[/] [{accent} bold]📋 计划审批[/] [{border}]{'─' * _SEP_WIDTH}[/]",
            classes="plan-border",
        )

        # 空行
        yield Static(f"[{border}]  │[/]", classes="plan-line")

        # 计划文件标题（类似 ToolApproval 的工具名）
        if plan_path:
            filename = Path(plan_path).name
            yield Static(
                f"[{border}]  │[/]   [{accent} bold]● {escape_markup(filename)}[/]",
                classes="plan-line",
            )
            yield Static(
                f"[{border}]  │[/]     [dim]{escape_markup(plan_path)}[/dim]",
                classes="plan-line",
            )

        # 计划内容
        plan_content = self._read_plan_file(plan_path)
        if plan_content:
            yield Markdown(plan_content, classes="plan-content")

        # 空行 + 分隔线
        yield Static(f"[{border}]  │[/]", classes="plan-line")
        yield Static(
            f"[{border}]  ├{'─' * (_SEP_WIDTH + 10)}[/]",
            classes="plan-border",
        )

        # 选项
        yield Static(
            self._render_options(),
            id="plan-approval-options",
            classes="plan-options",
            markup=False,
        )

        # 空行
        yield Static(f"[{border}]  │[/]", classes="plan-line")

        # 底部圆角 + 提示
        yield Static(
            f"[{border}]  ╰─[/] [dim]↑↓ 选择 · enter 确认 · esc 拒绝[/dim] [{border}]{'─' * (_SEP_WIDTH - 18)}[/]",
            classes="plan-border",
        )

    def on_mount(self) -> None:
        self.focus()

    def on_key(self, event) -> None:
        if event.key == "up":
            self._selected = (self._selected - 1) % len(_OPTIONS)
            self._refresh_options()
            event.stop()
        elif event.key == "down":
            self._selected = (self._selected + 1) % len(_OPTIONS)
            self._refresh_options()
            event.stop()
        elif event.key == "enter":
            decision = _OPTIONS[self._selected]["key"]
            self.post_message(self.Decided(decision))
            self.call_later(self.remove)
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decided("rejected"))
            self.call_later(self.remove)
            event.stop()

    def _render_options(self) -> Text:
        border = get_color("border_separator")
        result = Text()
        for i, opt in enumerate(_OPTIONS):
            if i > 0:
                result.append("\n")
            key = opt["key"]
            label = opt["label"]
            color = get_color(_OPTION_COLOR_ROLES.get(key, "foreground"))
            result.append("  │", style=border)
            if i == self._selected:
                result.append(f"   ❯ {label}", style=f"bold {color}")
            else:
                result.append(f"     {label}")
        return result

    def _refresh_options(self) -> None:
        self.query_one("#plan-approval-options", Static).update(self._render_options())

    @staticmethod
    def _read_plan_file(plan_path: str) -> str:
        if not plan_path:
            return ""
        try:
            return Path(plan_path).read_text(encoding="utf-8").strip()
        except Exception:
            logger.debug("无法读取计划文件: %s", plan_path, exc_info=True)
            return ""

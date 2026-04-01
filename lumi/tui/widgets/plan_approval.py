"""计划审批组件 - 用户审批 Agent 提交的实施计划

Agent 在计划模式完成后调用 exit_plan_mode 工具触发此组件，
用户可以批准计划（开始实施）或拒绝（继续修改计划）。
使用 Textual 原生 CSS border 实现自适应闭合边框。
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Markdown, Rule, Static

from lumi.tui.renderers.utils import escape_markup
from lumi.tui.theme import get_color
from lumi.tui.widgets.approval_base import BaseApproval

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


class PlanApproval(BaseApproval):
    """计划审批组件 - 键盘驱动的批准/拒绝选择器"""

    class Decided(BaseApproval.Decided):
        """计划审批决定（独立类型，确保 Textual 消息路由正确）"""

    DEFAULT_CSS = """
    PlanApproval {
        margin: 0 0 0 2;
        padding: 0 1;
        background: transparent;
        height: auto;
        border: round $accent;
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
        margin: 0 0 0 4;
        padding: 0;
        height: auto;
        max-height: 30;
        overflow-y: auto;
    }

    PlanApproval Rule {
        margin: 0;
        color: $accent;
    }
    """

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(
            options=_OPTIONS,
            option_color_roles=_OPTION_COLOR_ROLES,
            cancel_key="rejected",
            options_selector="#plan-approval-options",
            content_selector=".plan-content",
            classes="plan-approval",
        )
        self._data = interrupt_data
        self.border_title = "📋 计划审批"
        self.border_subtitle = "↑↓ 选择 · enter 确认 · esc 拒绝"

    def compose(self) -> ComposeResult:
        accent = get_color("accent")
        plan_path = self._data.get("plan_file_path", "")

        # 计划文件标题
        if plan_path:
            filename = Path(plan_path).name
            yield Static(
                f"[{accent} bold]● {escape_markup(filename)}[/]",
                classes="plan-line",
            )
            yield Static(
                f"  [dim]{escape_markup(plan_path)}[/dim]",
                classes="plan-line",
            )

        # 计划内容
        plan_content = self._read_plan_file(plan_path)
        if plan_content:
            yield Markdown(plan_content, classes="plan-content")

        # 分隔线
        yield Rule()

        # 选项
        yield Static(
            self._render_options(),
            id="plan-approval-options",
            classes="plan-options",
            markup=False,
        )

    @staticmethod
    def _read_plan_file(plan_path: str) -> str:
        if not plan_path:
            return ""
        try:
            return Path(plan_path).read_text(encoding="utf-8").strip()
        except Exception:
            logger.debug("无法读取计划文件: %s", plan_path, exc_info=True)
            return ""

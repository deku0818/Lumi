"""运行状态栏 — 显示 spinner、运行时长和 token 计数"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from lumi.tui.renderers.utils import SpinnerMixin
from lumi.tui.run_state import RunPhase

if TYPE_CHECKING:
    from lumi.tui.run_state import RunContext


# 各阶段的显示标签
_PHASE_LABELS: dict[RunPhase, str] = {
    RunPhase.THINKING: "Thinking…",
    RunPhase.STREAMING: "Generating…",
    RunPhase.TOOL_CALL_PENDING: "Thinking…",
    RunPhase.TOOL_RUNNING: "Running tools…",
    RunPhase.WAITING_ASK: "Waiting for input…",
    RunPhase.WAITING_APPROVAL: "Waiting for approval…",
}


class RunStatusBar(Static, SpinnerMixin):
    """输入栏上方的运行状态指示器。

    IDLE 时隐藏，运行时显示 1 行：
        ⠋ Generating… (12s · ↓ 156 tokens)

    持有 RunContext 引用，spinner tick 时直接读取最新的 elapsed 和 token 数。
    """

    DEFAULT_CSS = """
    RunStatusBar {
        margin: 0 0 0 1;
        padding: 0 1;
        height: 1;
        color: $text-muted;
        display: none;
    }
    RunStatusBar.-visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="run-status-bar")
        self._run_ctx: RunContext | None = None
        self._last_frame: str = "⠋"
        self._task_label: str = ""  # 当前 in_progress 任务名，空则用默认标签

    def bind_run_context(self, ctx: RunContext) -> None:
        """绑定 RunContext，之后 spinner tick 自动读取最新状态。"""
        self._run_ctx = ctx

    def on_mount(self) -> None:
        # spinner 在 show_running() 时按需启动，IDLE 期间不运行
        pass

    def _on_spinner_tick(self, frame_char: str) -> None:
        self._last_frame = frame_char
        ctx = self._run_ctx
        if ctx is None or ctx.phase == RunPhase.IDLE:
            return
        self._render_status(ctx.phase, ctx.elapsed, ctx.output_tokens)

    def show_running(self) -> None:
        """显示状态栏并启动 spinner。"""
        self._start_spinner()
        self.add_class("-visible")

    def hide(self) -> None:
        """隐藏状态栏并停止 spinner，避免 IDLE 期间空跑 timer。"""
        self._stop_spinner()
        self._task_label = ""
        self.remove_class("-visible")

    def set_task_label(self, label: str) -> None:
        """设置当前任务名，用于替换 Thinking… 等默认标签。

        Args:
            label: 任务名，空字符串表示恢复默认标签
        """
        self._task_label = label

    def _render_status(
        self, phase: RunPhase, elapsed: float, output_tokens: int
    ) -> None:
        label = _PHASE_LABELS.get(phase, "")
        if not label:
            return

        # 有 in_progress 任务时，THINKING/TOOL_RUNNING 阶段用任务名替换默认标签
        if self._task_label and phase in (
            RunPhase.THINKING,
            RunPhase.TOOL_CALL_PENDING,
            RunPhase.TOOL_RUNNING,
        ):
            label = self._task_label

        # 时间：<60s 显示秒，>=60s 显示 m:ss
        secs = int(elapsed)
        if secs < 60:
            time_str = f"{secs}s"
        else:
            time_str = f"{secs // 60}m{secs % 60:02d}s"

        # 组装括号内容
        meta_parts: list[str] = [time_str]
        if output_tokens > 0:
            meta_parts.append(f"↓ {output_tokens} tokens")

        meta = " · ".join(meta_parts)
        self.update(f"{self._last_frame} {label} ({meta})")

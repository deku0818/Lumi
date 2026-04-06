"""底部状态行 — 模型名称 · Token 用量 · 上下文进度条"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from lumi.tui.theme import get_color
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.tui.run_state import RunContext

# 上下文进度条长度
_BAR_LEN = 10
_FILLED = "⛃"
_EMPTY = "⛶"


def _format_tokens(n: int) -> str:
    """将 token 数格式化为可读字符串：1234 → 1,234, 12345 → 12.35k."""
    if n < 1000:
        return str(n)
    return f"{n / 1000:.2f}k"


def _format_max(n: int) -> str:
    """将上下文上限格式化为可读字符串：200000 → 200k."""
    if n >= 1000:
        k = n // 1000
        return f"{k}k"
    return str(n)


class StatusLine(Static):
    """输入栏下方的持久状态行。

    显示内容：
        qwen3-max │ ⛁ 11.84k (r:8.2k w:1.1k) │ ████░░░░░░ 5% of 200k
    """

    DEFAULT_CSS = """
    StatusLine {
        dock: bottom;
        height: 1;
        padding: 0 3;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="status-line")
        self._run_ctx: RunContext | None = None
        self._model_name: str = ""
        self._context_max: int = 200000

    def configure(
        self,
        run_ctx: RunContext,
        model_name: str,
        context_max: int,
    ) -> None:
        """绑定运行上下文和模型信息。"""
        self._run_ctx = run_ctx
        self._model_name = model_name
        if not isinstance(context_max, int) or context_max <= 0:
            logger.warning(
                "[StatusLine] Invalid context_max=%r, defaulting to 200000",
                context_max,
            )
            context_max = 200000
        self._context_max = context_max
        self.refresh_display()

    def refresh_display(self) -> None:
        """刷新状态行内容。"""
        ctx = self._run_ctx
        muted = get_color("text_muted")
        accent = get_color("accent")

        model = self._model_name or "unknown"
        total = ctx.total_tokens if ctx else 0
        input_tokens = ctx.input_tokens if ctx else 0
        sep = " [dim]│[/dim] "

        # token 用量
        token_str = _format_tokens(total)
        max_str = _format_max(self._context_max)

        # 上下文进度条
        ratio = min(input_tokens / self._context_max, 1.0)
        pct = int(ratio * 100)
        filled = max(int(ratio * _BAR_LEN), 1) if input_tokens > 0 else 0
        empty = _BAR_LEN - filled
        bar = f"[{accent}]{_FILLED * filled}[/][{muted}]{_EMPTY * empty}[/]"

        parts = [
            f"[{muted}]{model}[/]",
            f"[{muted}]⛁ {token_str}/{max_str}[/]",
            f"{bar} [{muted}]{pct}%[/]",
        ]

        # cache 信息（有缓存时才追加）
        cache_parts = self._format_cache_parts(ctx)
        if cache_parts:
            parts.append(f"[{muted}]cache {','.join(cache_parts)}[/]")

        self.update(sep.join(parts))

    @staticmethod
    def _format_cache_parts(ctx: RunContext | None) -> list[str]:
        """格式化 cache token 信息片段。"""
        if ctx is None:
            return []
        result: list[str] = []
        if ctx.cache_read_tokens:
            result.append(f"r:{_format_tokens(ctx.cache_read_tokens)}")
        if ctx.cache_creation_tokens:
            result.append(f"w:{_format_tokens(ctx.cache_creation_tokens)}")
        return result

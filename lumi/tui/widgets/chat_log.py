"""可滚动对话区域

支持旧消息压缩：当 DOM 子节点超过阈值时，将最早的消息替换为轻量占位符，
减少布局计算量，改善滚动流畅度。
"""

from __future__ import annotations

from typing import Final

from rich.text import Text
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import Static

from lumi.tui.theme import get_color
from lumi.utils.logger import logger

# 保留在 DOM 中的最大 widget 数量（超出后压缩旧消息）
_MAX_LIVE_WIDGETS: Final[int] = 80

# 压缩后保留的 widget 数量（留出余量避免频繁触发）
_KEEP_WIDGETS: Final[int] = 50

# 压缩占位符的 CSS class
_COMPACTED_CLASS: Final[str] = "compacted-placeholder"


class ChatLog(VerticalScroll):
    """可滚动的对话日志区域

    当子节点数超过 _MAX_LIVE_WIDGETS 时，自动将最早的消息替换为
    轻量 Static 占位符，控制 DOM 规模以保持滚动流畅。
    """

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
    }
    """

    _SCROLL_THROTTLE: float = 0.1

    def __init__(self, **kwargs) -> None:
        super().__init__(id="chat-log", **kwargs)
        self._auto_scroll = True
        self._scroll_pending = False
        self._scroll_timer: Timer | None = None
        self._compact_scheduled = False

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """监听滚动位置变化，判断用户是否主动离开底部。

        向上滚动（new < old）且不在底部附近 → 禁用自动滚动。
        到达底部 → 恢复自动滚动。
        """
        # 必须调用 super() — 父类负责同步 scrollbar 位置和刷新视口
        super().watch_scroll_y(old_value, new_value)
        if self._scroll_pending:
            # 由 auto_scroll 触发的滚动，不改变状态
            return
        at_bottom = new_value >= self.max_scroll_y - 2
        if at_bottom:
            self._auto_scroll = True
        elif new_value < old_value:
            # 用户向上滚动
            self._auto_scroll = False

    async def scroll_to_end(self) -> None:
        """滚动到底部并重新启用自动滚动"""
        self._auto_scroll = True
        self._scroll_pending = True
        self.scroll_end(animate=False)
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._scroll_timer = self.set_timer(0.1, self._clear_scroll_pending)

    async def auto_scroll_if_needed(self) -> None:
        """节流式自动滚动：合并短时间内的多次调用为一次 scroll_end。"""
        if not self._auto_scroll or self._scroll_pending:
            return
        self._scroll_pending = True
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._scroll_timer = self.set_timer(self._SCROLL_THROTTLE, self._do_scroll)

    def _do_scroll(self) -> None:
        """定时器回调：执行实际的滚动操作。"""
        try:
            if self._auto_scroll:
                self.scroll_end(animate=False)
            if self._scroll_timer:
                self._scroll_timer.stop()
            self._scroll_timer = self.set_timer(0.1, self._clear_scroll_pending)
        except Exception as e:
            self._scroll_pending = False
            self._scroll_timer = None
            logger.error("Scroll failed (error=%s)", type(e).__name__, exc_info=True)

    def _clear_scroll_pending(self) -> None:
        """清除 scroll pending 标记。"""
        self._scroll_pending = False

    # ── 旧消息压缩 ──

    def schedule_compact(self) -> None:
        """延迟触发旧消息压缩（合并短时间内的多次挂载）。"""
        if self._compact_scheduled:
            return
        self._compact_scheduled = True
        self.set_timer(0.5, self._do_compact)

    def _do_compact(self) -> None:
        """将超出阈值的旧消息替换为轻量占位符。

        保留最后 _KEEP_WIDGETS 个 widget，前面的全部移除，
        插入一个 Static 占位符显示被隐藏的消息数。
        已有的占位符会被合并计数。
        """
        self._compact_scheduled = False
        children = list(self.children)
        if len(children) <= _MAX_LIVE_WIDGETS:
            return

        to_remove = children[: len(children) - _KEEP_WIDGETS]
        if not to_remove:
            return

        compacted_count = self._count_compacted(to_remove)
        for w in to_remove:
            w.remove()

        placeholder = Static(
            Text(
                f"  ↑ {compacted_count} 条早期消息已折叠",
                style=f"italic {get_color('text_muted')}",
            ),
            markup=False,
            classes=_COMPACTED_CLASS,
        )
        placeholder._compacted_count = compacted_count  # type: ignore[attr-defined]
        self.mount(placeholder, before=0)

        logger.debug(
            "ChatLog compacted: removed %d widgets, %d messages hidden",
            len(to_remove),
            compacted_count,
        )

    @staticmethod
    def _count_compacted(widgets: list) -> int:
        """统计被压缩的消息数（合并已有占位符的计数）。"""
        count = 0
        for w in widgets:
            if w.has_class(_COMPACTED_CLASS):
                count += getattr(w, "_compacted_count", 0)
            else:
                count += 1
        return count

    async def append_error(self, message: str, detail: str = "") -> None:
        """在聊天日志中追加错误提示行。

        Args:
            message: 错误前缀文本（如 "初始化失败"）
            detail: 错误详情（可选）
        """
        try:
            err = Text()
            err.append(f"✗ {message}", style=f"bold {get_color('error')}")
            if detail:
                err.append(f" {detail}", style=get_color("error"))
            await self.mount(Static(err, markup=False))
            await self.auto_scroll_if_needed()
        except Exception as e:
            logger.error(
                "Failed to append error message: %s (detail=%s, error=%s)",
                message,
                detail,
                type(e).__name__,
                exc_info=True,
            )

    async def append_hint(self, prefix: str, text: str, *, style: str = "dim") -> None:
        """在聊天日志中追加提示行（如中断、面板关闭等）。

        Args:
            prefix: 前缀字符（如 "● "、"└ "）
            text: 提示文本
            style: Rich 样式字符串
        """
        try:
            hint = Text()
            hint.append(prefix, style=style)
            hint.append(text, style=style)
            widget = Static(hint, markup=False)
            widget.styles.padding = (0, 1)
            await self.mount(widget)
            await self.auto_scroll_if_needed()
        except Exception as e:
            logger.error(
                "Failed to append hint: prefix=%r text=%r (error=%s)",
                prefix,
                text,
                type(e).__name__,
                exc_info=True,
            )

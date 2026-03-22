"""可滚动对话区域"""

from rich.text import Text
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import Static

from lumi.tui.theme import get_color
from lumi.utils.logger import logger


class ChatLog(VerticalScroll):
    """可滚动的对话日志区域"""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
    }
    """

    _SCROLL_THROTTLE: float = 0.05

    def __init__(self, **kwargs) -> None:
        super().__init__(id="chat-log", **kwargs)
        self._auto_scroll = True
        self._scroll_pending = False
        self._scroll_timer: Timer | None = None

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """监听滚动位置变化，判断用户是否主动离开底部。

        向上滚动（new < old）且不在底部附近 → 禁用自动滚动。
        到达底部 → 恢复自动滚动。
        """
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

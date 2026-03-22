"""可滚动对话区域"""

from rich.text import Text
from textual.containers import VerticalScroll
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

    # scroll 节流间隔（秒）— 合并高频事件中的多次 scroll 为一次
    _SCROLL_THROTTLE: float = 0.05

    def __init__(self, **kwargs) -> None:
        super().__init__(id="chat-log", **kwargs)
        self._auto_scroll = True
        self._scroll_pending = False

    def on_scroll_up(self) -> None:
        """用户手动上滚时禁用自动滚动"""
        self._auto_scroll = False

    def on_scroll_change(self) -> None:
        """滚动位置变化时，若已到底部则重新启用自动滚动"""
        at_bottom = self.scroll_offset.y >= self.max_scroll_offset.y - 1
        if at_bottom:
            self._auto_scroll = True

    async def scroll_to_end(self) -> None:
        """滚动到底部并重新启用自动滚动"""
        self._auto_scroll = True
        self.scroll_end(animate=False)

    async def auto_scroll_if_needed(self) -> None:
        """节流式自动滚动：合并短时间内的多次调用为一次 scroll_end。"""
        if not self._auto_scroll or self._scroll_pending:
            return
        self._scroll_pending = True
        self.set_timer(self._SCROLL_THROTTLE, self._do_scroll)

    def _do_scroll(self) -> None:
        """定时器回调：执行实际的滚动操作。"""
        try:
            self._scroll_pending = False
            if self._auto_scroll:
                self.scroll_end(animate=False)
        except Exception:
            logger.debug("Scroll failed", exc_info=True)

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
        except Exception:
            logger.warning(
                "Failed to append error message: %s (detail=%s)",
                message,
                detail,
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
        except Exception:
            logger.warning(
                "Failed to append hint: prefix=%r text=%r",
                prefix,
                text,
                exc_info=True,
            )

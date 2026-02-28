"""可滚动对话区域"""

from textual.containers import VerticalScroll


class ChatLog(VerticalScroll):
    """可滚动的对话日志区域"""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(id="chat-log", **kwargs)
        self._auto_scroll = True

    def on_scroll_up(self) -> None:
        """用户手动上滚时禁用自动滚动"""
        self._auto_scroll = False

    async def scroll_to_end(self) -> None:
        """滚动到底部并重新启用自动滚动"""
        self._auto_scroll = True
        self.scroll_end(animate=False)

    async def auto_scroll_if_needed(self) -> None:
        """如果自动滚动开启，则滚到底部"""
        if self._auto_scroll:
            self.scroll_end(animate=False)

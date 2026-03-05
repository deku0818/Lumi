"""思考中动画指示器"""

from textual.widgets import Static

from lumi.tui.renderers.utils import SPINNER_FRAMES


class ThinkingIndicator(Static):
    """思考中指示器 - 显示旋转动画"""

    DEFAULT_CSS = """
    ThinkingIndicator {
        margin: 0 0 0 2;
        padding: 0 1;
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__("", classes="thinking-indicator")
        self._frame = 0
        self._timer = None
        self._stopped = False

    def on_mount(self) -> None:
        if self._stopped:
            self.remove()
            return
        self._timer = self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self.update(f"{frame} Thinking...")
        self._frame += 1

    def stop(self) -> None:
        """停止动画并移除"""
        self._stopped = True
        if self._timer:
            self._timer.stop()
        if self.is_mounted:
            self.remove()

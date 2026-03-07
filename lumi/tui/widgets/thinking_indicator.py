"""思考中动画指示器"""

from textual.widgets import Static

from lumi.tui.renderers.utils import SpinnerMixin


class ThinkingIndicator(Static, SpinnerMixin):
    """思考中指示器 — 用 CSS class 切换可见性，避免频繁 mount/remove。

    生命周期:
        mount 后 spinner 立即运行；通过 ``show()`` / ``hide()`` 控制可见性；
        ``teardown()`` 停止 spinner 并从 DOM 移除。
    """

    DEFAULT_CSS = """
    ThinkingIndicator {
        margin: 0 0 0 1;
        padding: 0 1;
        height: 1;
        color: $text-muted;
    }
    ThinkingIndicator.-hidden {
        display: none;
    }
    """

    def __init__(self) -> None:
        super().__init__("", classes="thinking-indicator")

    def on_mount(self) -> None:
        self._start_spinner()

    def _on_spinner_tick(self, frame_char: str) -> None:
        if self.has_class("-hidden"):
            return
        self.update(f"{frame_char} Thinking...")

    def show(self) -> None:
        self._spinner_frame = 0
        self.remove_class("-hidden")

    def hide(self) -> None:
        self.add_class("-hidden")

    def teardown(self) -> None:
        self._stop_spinner()
        if self.is_mounted:
            self.remove()

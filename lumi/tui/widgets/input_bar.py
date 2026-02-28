"""底部输入栏"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.widgets import Input, Static


_TOOL_MODES = ("approve", "auto")

_MODE_DISPLAY = {
    "approve": ("🛡 approve mode", "#4caf50"),
    "auto": ("⚡ auto mode", "#ffcc00"),
}


class InputBar(Vertical):
    """底部输入栏 - 带 > 提示符 + 模式指示器"""

    # 样式由 APP_CSS (#input-area) 统一管理

    class Submitted(Message):
        """用户提交消息"""

        def __init__(self, text: str, tool_mode: str) -> None:
            super().__init__()
            self.text = text
            self.tool_mode = tool_mode

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tool_mode = "approve"

    def compose(self) -> ComposeResult:
        with Horizontal(id="input-row"):
            yield Static("> ", id="prompt-label")
            yield Input(placeholder="输入消息...", id="user-input")
        label, color = _MODE_DISPLAY[self._tool_mode]
        yield Static(
            f"[{color}]{label}[/] [dim](shift+tab to switch)[/dim]",
            id="mode-indicator",
        )

    def on_mount(self) -> None:
        self.query_one("#user-input", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "shift+tab":
            event.prevent_default()
            event.stop()
            self.action_switch_tool_mode()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Input 原生 Enter 提交"""
        text = event.value.strip()
        if text:
            event.input.value = ""
            self.post_message(self.Submitted(text, self._tool_mode))

    def action_switch_tool_mode(self) -> None:
        """循环切换 tool_mode"""
        idx = _TOOL_MODES.index(self._tool_mode)
        self._tool_mode = _TOOL_MODES[(idx + 1) % len(_TOOL_MODES)]
        self._update_mode_indicator()

    def _update_mode_indicator(self) -> None:
        label, color = _MODE_DISPLAY[self._tool_mode]
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[{color}]{label}[/] [dim](shift+tab to switch)[/dim]")

    def set_tool_mode(self, mode: str) -> None:
        """外部设置 tool_mode 并更新指示器"""
        if mode in _TOOL_MODES:
            self._tool_mode = mode
            self._update_mode_indicator()

    def set_disabled(self, disabled: bool) -> None:
        """禁用/启用输入"""
        inp = self.query_one("#user-input", Input)
        inp.disabled = disabled
        if not disabled:
            inp.focus()

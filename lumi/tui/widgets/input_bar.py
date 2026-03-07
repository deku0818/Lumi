"""底部输入栏"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.widgets import Input, Static

from lumi.tui.theme import get_color

_TOOL_MODES = ("approve", "auto", "privileged")

# 值为 (label, 语义角色名)，颜色在渲染时通过 get_color() 解析
_MODE_DISPLAY: dict[str, tuple[str, str]] = {
    "approve": ("⏸ approve mode", "#E8D888"),
    "auto": ("▶ auto mode", "#88E8A0"),
    "privileged": ("▶▶ privileged mode ⚠", "#88A0E8"),
}


def _resolve_color(role_or_hex: str) -> str:
    """解析颜色：如果是 hex 值直接返回，否则通过 get_color 查找。"""
    if role_or_hex.startswith("#"):
        return role_or_hex
    return get_color(role_or_hex)


class InputBox(Static):
    """输入框容器 - 带边框，类似 TitleBlock"""

    DEFAULT_CSS = """
    InputBox {
        height: auto;
        border-title-style: bold;
        padding: 0 1;
        border: round $accent;
        border-title-color: $accent;
    }

    #input-row {
        height: auto;
    }

    InputBox #prompt-label {
        text-style: bold;
        width: 3;
        height: 1;
        padding: 0;
        color: $accent;
    }

    InputBox Input {
        background: transparent;
        border: none !important;
        width: 1fr;
        height: 1;
        padding: 0;
        margin: 0;
        color: $foreground;
    }

    InputBox Input:focus {
        border: none !important;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="input-row"):
            yield Static("> ", id="prompt-label")
            yield Input(placeholder="输入消息...", id="user-input")


class InputBar(Vertical):
    """底部输入栏 - 带 > 提示符 + 模式指示器"""

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        max-height: 10;
        background: transparent;
        padding: 0 2 1 2;
    }

    #status-row {
        height: 1;
        padding: 0 0 0 1;
    }

    #mode-indicator {
        width: 1fr;
        height: 1;
        color: $text-muted;
    }

    #bell-indicator {
        width: auto;
        height: 1;
        color: $text-muted;
        padding: 0 1 0 0;
    }
    """

    class Submitted(Message):
        """用户提交消息"""

        def __init__(self, text: str, tool_mode: str) -> None:
            super().__init__()
            self.text = text
            self.tool_mode = tool_mode

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tool_mode = "approve"
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""  # 暂存当前未提交的输入

    def compose(self) -> ComposeResult:
        yield InputBox()
        label, role = _MODE_DISPLAY[self._tool_mode]
        color = _resolve_color(role)
        with Horizontal(id="status-row"):
            yield Static(
                f"[{color}]{label}[/] [dim](shift+tab to switch)[/dim]",
                id="mode-indicator",
            )
            yield Static("[#B888E8]⚑[/]", id="bell-indicator")

    def on_mount(self) -> None:
        self.query_one(InputBox).border_title = "Input"
        self.query_one("#user-input", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "shift+tab":
            event.prevent_default()
            event.stop()
            self.action_switch_tool_mode()
        elif event.key == "up":
            event.prevent_default()
            event.stop()
            self._navigate_history(-1)
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self._navigate_history(1)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Input 原生 Enter 提交"""
        text = event.value.strip()
        if text:
            self._history.append(text)
            self._history_index = -1
            self._draft = ""
            event.input.value = ""
            self.post_message(self.Submitted(text, self._tool_mode))

    def _navigate_history(self, direction: int) -> None:
        """上下键浏览输入历史

        Args:
            direction: -1 向上（更早），1 向下（更近）
        """
        if not self._history:
            return
        inp = self.query_one("#user-input", Input)
        # 首次按上键时，暂存当前输入
        if self._history_index == -1 and direction == -1:
            self._draft = inp.value
        new_index = self._history_index - direction
        if new_index < 0:
            # 回到当前草稿
            self._history_index = -1
            inp.value = self._draft
        elif new_index >= len(self._history):
            return
        else:
            self._history_index = new_index
            inp.value = self._history[len(self._history) - 1 - new_index]
        # 光标移到末尾
        inp.cursor_position = len(inp.value)

    def action_switch_tool_mode(self) -> None:
        """循环切换 tool_mode"""
        idx = _TOOL_MODES.index(self._tool_mode)
        self._tool_mode = _TOOL_MODES[(idx + 1) % len(_TOOL_MODES)]
        self._update_mode_indicator()

    def _update_mode_indicator(self) -> None:
        label, role = _MODE_DISPLAY[self._tool_mode]
        color = _resolve_color(role)
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

    def update_bell(self, unread: int) -> None:
        """更新铃铛指示器的未读数量。

        Args:
            unread: 未读通知数量。
        """
        bell = self.query_one("#bell-indicator", Static)
        if unread > 0:
            bell.update(f"[#B888E8]⚑ {unread}[/]")
        else:
            bell.update("[#B888E8]⚑[/]")

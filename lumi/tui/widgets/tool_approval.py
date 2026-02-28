"""工具审批组件"""

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static


class ToolApproval(Vertical):
    """工具审批组件 - 键盘驱动的列表选择器"""

    can_focus = True

    _options: tuple[tuple[str, str], ...] = (
        ("approve", "允许本次执行"),
        ("auto", "允许本次会话内的所有工具调用"),
        ("reject", "拒绝"),
    )

    _OPTION_COLORS: dict[str, str] = {
        "approve": "#4caf50",
        "auto": "#ffcc00",
        "reject": "#ef5350",
    }

    DEFAULT_CSS = """
    ToolApproval {
        margin: 0 1 0 2;
        padding: 1;
        background: #18182a;
        border: solid #ffcc00;
        height: auto;
    }

    ToolApproval .approval-label {
        color: #ffcc00;
        text-style: bold;
        margin: 0 0 1 0;
    }

    ToolApproval .tool-call-info {
        color: #888899;
        margin: 0 0 1 1;
    }

    ToolApproval .approval-options {
        height: auto;
        margin: 0 0 0 2;
    }

    ToolApproval .approval-hint {
        color: #555566;
        margin: 1 0 0 2;
    }
    """

    class Decided(Message):
        """用户做出审批决定"""

        def __init__(self, decision: str) -> None:
            super().__init__()
            self.decision = decision  # "approve" | "auto" | "reject"

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(classes="tool-approval")
        self._data = interrupt_data
        self._selected = 0

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold #ffcc00]⚠[/] {escape(self._data.get('message', '是否执行以下工具？'))}",
            classes="approval-label",
        )
        for tc in self._data.get("tool_calls", []):
            name = tc.get("name", "unknown")
            args_preview = str(tc.get("args", {}))
            if len(args_preview) > 100:
                args_preview = args_preview[:100] + "..."
            yield Static(
                f"  • {escape(name)}({escape(args_preview)})", classes="tool-call-info"
            )
        yield Static(
            self._render_options(), id="approval-options", classes="approval-options"
        )
        yield Static(
            "[dim](↑↓ 选择, enter 确认, esc 拒绝)[/dim]", classes="approval-hint"
        )

    def on_mount(self) -> None:
        self.focus()

    def on_key(self, event) -> None:
        if event.key == "up":
            self._selected = (self._selected - 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "down":
            self._selected = (self._selected + 1) % len(self._options)
            self._refresh_options()
            event.stop()
        elif event.key == "enter":
            decision = self._options[self._selected][0]
            self.post_message(self.Decided(decision))
            self.remove()
            event.stop()
        elif event.key == "escape":
            self.post_message(self.Decided("reject"))
            self.remove()
            event.stop()

    def _render_options(self) -> str:
        lines = []
        for i, (key, label) in enumerate(self._options):
            color = self._OPTION_COLORS[key]
            if i == self._selected:
                lines.append(f"[bold {color}]● {label}[/]")
            else:
                lines.append(f"  {label}")
        return "\n".join(lines)

    def _refresh_options(self) -> None:
        self.query_one("#approval-options", Static).update(self._render_options())

"""Ask 工具展示块 - 不可折叠，平铺显示问答"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from lumi.tui.theme import get_color
from lumi.tui.widgets.ask_dialog import AskDialog

_DECLINED_TEXT = "User declined to answer questions"


class AskBlock(Vertical):
    """Ask 工具展示块

    不可折叠，标题显示 ask(A question) 或 ask(N questions)。
    - 选择中：标题 + AskDialog
    - 回答后：标题 + 灰色结果摘要
    """

    DEFAULT_CSS = """
    AskBlock {
        margin: 0 1 0 0;
        padding: 0 1;
        height: auto;
    }

    AskBlock .ask-block-title {
        height: auto;
        padding: 0;
        margin: 0;
    }

    AskBlock .ask-block-result {
        padding: 0 0 0 2;
        margin: 0;
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__()
        self._interrupt_data = interrupt_data
        questions = interrupt_data.get("questions", [])
        count = len(questions)
        self._label = "A question" if count <= 1 else f"{count} questions"

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{get_color('text_muted')}]●[/] [bold {get_color('accent')}]ask[/]({self._label})",
            classes="ask-block-title",
            id=f"ask-title-{id(self)}",
        )
        yield AskDialog(self._interrupt_data)
        yield Static("", classes="ask-block-result", id=f"ask-result-{id(self)}")

    def set_result(self, answer: str) -> None:
        """用户回答完成，更新状态"""
        is_declined = answer == _DECLINED_TEXT
        display = _DECLINED_TEXT if is_declined else answer
        if len(display) > 500:
            display = display[:500] + "..."

        # 更新状态圆点
        dot = (
            f"[{get_color('error')}]●[/]"
            if is_declined
            else f"[{get_color('success')}]●[/]"
        )
        self.query_one(f"#ask-title-{id(self)}", Static).update(
            f"{dot} [bold {get_color('accent')}]ask[/]({self._label})"
        )

        for dialog in self.query(AskDialog):
            dialog.remove()
        self.query_one(f"#ask-result-{id(self)}", Static).update(display)

    def on_ask_dialog_tab_changed(self, event: AskDialog.TabChanged) -> None:
        """消费 TabChanged 事件，避免冒泡"""
        event.stop()

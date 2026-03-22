"""AskDialog 组件 Textual pilot 测试

验证所有交互路径：
- ↑↓ 导航到自定义输入项 + Enter 激活
- 数字键激活自定义输入
- 输入文本后 Enter 保存并退出
- Escape 取消输入
- 多选 checkbox 显示
- 自定义文本显示（输入后替代 "Type something"）
- Tab 切题保留自定义文本
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Input

from lumi.tui.widgets.ask_dialog import AskDialog


def _make_interrupt(
    *,
    multi: bool = False,
    questions: int = 1,
) -> dict:
    """构造 interrupt_data"""
    qs = []
    for i in range(questions):
        qs.append(
            {
                "id": i,
                "question": f"Question {i + 1}?",
                "header": f"Q{i + 1}",
                "options": [
                    {"label": "Alpha", "description": "Desc A"},
                    {"label": "Beta", "description": "Desc B"},
                    {"label": "Gamma", "description": "Desc C"},
                    {"label": "", "description": "输入内容"},
                ],
                "multiSelect": multi,
            }
        )
    return {"type": "ask", "tool_call_id": "tc_test", "questions": qs}


class AskDialogTestApp(App):
    """测试用 App，挂载 AskDialog"""

    CSS = """
    Screen { layout: vertical; }
    AskDialog { width: 100%; height: auto; }
    """

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__()
        self._data = interrupt_data
        self.answered: str | None = None

    def compose(self) -> ComposeResult:
        yield AskDialog(self._data)

    def on_ask_dialog_answered(self, event: AskDialog.Answered) -> None:
        self.answered = event.answer
        self.exit()


# ── 单选基础测试 ──


async def test_single_select_by_enter():
    """单选：↓ 移动到第二项，Enter 选中并提交"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        await pilot.press("down")  # 移到 Beta
        await pilot.press("enter")
        await pilot.pause()
    assert app.answered is not None
    assert "Beta" in app.answered


async def test_single_select_by_digit():
    """单选：数字键 2 直接选中 Beta 并提交"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
    assert app.answered is not None
    assert "Beta" in app.answered


# ── 自定义输入测试 ──


async def test_custom_input_via_arrow_enter():
    """↓ 导航到自定义输入项 → Enter 激活 → 输入文本 → Enter 退出"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # 3 个有效选项，自定义输入在第 4 位（index 3）
        await pilot.press("down")  # 1 → Beta
        await pilot.press("down")  # 2 → Gamma
        await pilot.press("down")  # 3 → 自定义输入
        assert dialog._highlighted[0] == 3

        await pilot.press("enter")  # 激活输入框
        await pilot.pause()
        assert dialog._input_focused is True

        # 输入行应可见
        inp = dialog.query_one("#ask-input-0", Input)
        assert inp.display is True

        # 输入文本
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()

        # Enter 退出输入模式
        await pilot.press("enter")
        await pilot.pause()
        assert dialog._input_focused is False
        assert dialog._custom_text[0] == "hello"


async def test_custom_input_via_digit():
    """数字键 4 激活自定义输入"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("4")  # 第 4 项是自定义输入
        await pilot.pause()
        assert dialog._input_focused is True


async def test_custom_input_escape_cancels():
    """Escape 取消输入，不保存空文本"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("4")  # 激活
        await pilot.pause()
        assert dialog._input_focused is True

        await pilot.press("escape")
        await pilot.pause()
        assert dialog._input_focused is False
        # 没输入内容，custom_text 应为空
        assert dialog._custom_text[0] == ""


async def test_custom_input_preserves_text():
    """输入文本后退出，再次进入应保留之前的文本"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # 第一次输入
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("a", "b", "c")
        await pilot.press("enter")
        await pilot.pause()
        assert dialog._custom_text[0] == "abc"

        # 选项列表中应显示 "abc" 而非 "Type something"
        opts_text = dialog._render_options_list(0)
        assert "abc" in opts_text
        assert "Type something" not in opts_text


async def test_custom_input_submit():
    """自定义输入有文本时，高亮该项按 Enter 直接提交"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # 输入文本
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("t", "e", "s", "t")
        await pilot.press("enter")  # 退出输入模式
        await pilot.pause()
        assert dialog._custom_text[0] == "test"

        # 此时高亮应在自定义输入项上，再按 Enter 提交
        assert dialog._highlighted[0] == 3
        await pilot.press("enter")
        await pilot.pause()
    assert app.answered is not None
    assert "test" in app.answered


# ── 多选测试 ──


async def test_multiselect_checkbox():
    """多选模式：空格切换 checkbox"""
    app = AskDialogTestApp(_make_interrupt(multi=True))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # 选中第一项
        await pilot.press("space")
        assert 0 in dialog._selected[0]

        # 选中第二项
        await pilot.press("down")
        await pilot.press("space")
        assert 1 in dialog._selected[0]

        # 取消第一项
        await pilot.press("up")
        await pilot.press("space")
        assert 0 not in dialog._selected[0]


async def test_multiselect_custom_input_checkbox():
    """多选模式：自定义输入项也有 checkbox 前缀"""
    app = AskDialogTestApp(_make_interrupt(multi=True))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        opts_text = dialog._render_options_list(0)
        # 自定义输入行应有 ○ 前缀（未选中）
        lines = opts_text.split("\n")
        last_line = lines[-1]
        assert "○" in last_line

        # 输入文本后应变为 ◉
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("enter")
        await pilot.pause()

        opts_text = dialog._render_options_list(0)
        lines = opts_text.split("\n")
        last_line = lines[-1]
        assert "◉" in last_line


# ── 多问题 Tab 切换 ──


async def test_tab_switch_preserves_custom_text():
    """多问题：切换 Tab 后自定义文本保留"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # Q1 输入自定义文本
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("q", "1")
        await pilot.press("enter")
        await pilot.pause()
        assert dialog._custom_text[0] == "q1"

        # 选择一个选项跳到 Q2
        await pilot.press("1")
        await pilot.pause()
        assert dialog._current_tab == 1

        # Q2 输入自定义文本
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("q", "2")
        await pilot.press("enter")
        await pilot.pause()

        # 切回 Q1 检查文本保留
        await pilot.press("left")
        await pilot.pause()
        assert dialog._current_tab == 0
        assert dialog._custom_text[0] == "q1"
        assert dialog._custom_text[1] == "q2"


# ── Escape 拒绝 ──


async def test_escape_declines():
    """Escape 拒绝回答"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.answered is not None
    from lumi.agents.tools.providers.ask import ASK_CANCELLED

    assert app.answered == ASK_CANCELLED


# ── Input 行可见性 ──


async def test_input_row_hidden_by_default():
    """Input 行默认隐藏"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.pause()
        row = dialog.query_one("#ask-input-row-0")
        assert row.display is False


async def test_input_row_visible_when_active():
    """激活后 Input 行可见"""
    app = AskDialogTestApp(_make_interrupt())
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("4")
        await pilot.pause()
        row = dialog.query_one("#ask-input-row-0")
        assert row.display is True


# ── Submit 确认页测试 ──


async def test_multi_question_goes_to_submit_tab():
    """多问题：最后一题选择后跳到 Submit 确认页，不直接提交"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # Q1 选择 Alpha → 跳到 Q2
        await pilot.press("1")
        await pilot.pause()
        assert dialog._current_tab == 1

        # Q2 选择 Beta → 跳到 Submit tab（不直接提交）
        await pilot.press("2")
        await pilot.pause()
        assert dialog._current_tab == 2  # Submit tab
        assert app.answered is None  # 还没提交


async def test_submit_tab_enter_submits():
    """Submit 确认页：Enter 提交"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("1")  # Q1 → Q2
        await pilot.pause()
        await pilot.press("2")  # Q2 → Submit
        await pilot.pause()
        assert dialog._current_tab == 2

        # Enter 提交
        await pilot.press("enter")
        await pilot.pause()
    assert app.answered is not None
    assert "Alpha" in app.answered
    assert "Beta" in app.answered


async def test_submit_tab_left_goes_back():
    """Submit 确认页：← 返回最后一题"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("1")  # Q1 → Q2
        await pilot.pause()
        await pilot.press("2")  # Q2 → Submit
        await pilot.pause()
        assert dialog._current_tab == 2

        # ← 返回 Q2
        await pilot.press("left")
        await pilot.pause()
        assert dialog._current_tab == 1
        assert app.answered is None


async def test_submit_tab_escape_declines():
    """Submit 确认页：Escape 拒绝"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        await pilot.press("1")  # Q1 → Q2
        await pilot.pause()
        await pilot.press("2")  # Q2 → Submit
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
    from lumi.agents.tools.providers.ask import ASK_CANCELLED

    assert app.answered == ASK_CANCELLED


async def test_submit_tab_shows_summary():
    """Submit 确认页：显示答案摘要"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        await pilot.press("1")  # Q1 选 Alpha → Q2
        await pilot.pause()
        await pilot.press("3")  # Q2 选 Gamma → Submit
        await pilot.pause()

        summary = dialog._render_submit_summary()
        assert "Alpha" in summary
        assert "Gamma" in summary
        assert "Q1" in summary
        assert "Q2" in summary


async def test_single_question_skips_submit_tab():
    """单问题：选择后直接提交，不经过 Submit 确认页"""
    app = AskDialogTestApp(_make_interrupt(questions=1))
    async with app.run_test() as pilot:
        await pilot.press("1")
        await pilot.pause()
    assert app.answered is not None
    assert "Alpha" in app.answered


async def test_right_arrow_to_submit_tab():
    """多问题：→ 可以导航到 Submit tab"""
    app = AskDialogTestApp(_make_interrupt(questions=2))
    async with app.run_test() as pilot:
        dialog = app.query_one(AskDialog)
        # → 到 Q2
        await pilot.press("right")
        await pilot.pause()
        assert dialog._current_tab == 1

        # → 到 Submit
        await pilot.press("right")
        await pilot.pause()
        assert dialog._current_tab == 2

"""Ask 中断的内联问答组件"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Input, Static

from lumi.tui.theme import get_color


class AskDialog(Vertical):
    """Ask 中断的内联问答组件

    支持：
    - 多问题 Tab 导航
    - 单选（选中即确认）与多选（checkbox）模式
    - 自定义文本输入
    - 键盘操作：↑↓ 移动、空格切换多选、数字键快选、Enter 确认
    """

    can_focus = True

    DEFAULT_CSS = """
    AskDialog {
        margin: 0;
        padding: 0;
        background: transparent;
        height: auto;
    }

    AskDialog .ask-tabs {
        height: auto;
        margin: 0 0 1 0;
    }

    AskDialog .ask-question {
        text-style: bold;
        margin: 0 0 1 0;
        color: $accent;
    }

    AskDialog .ask-body {
        height: auto;
    }

    AskDialog .ask-options-list {
        height: auto;
        margin: 0 0 0 1;
    }

    AskDialog .ask-custom-row {
        height: auto;
        margin: 0 0 0 1;
    }

    AskDialog Input {
        width: 1fr;
        background: $panel;
        color: $foreground;
        border: solid $border-blurred;
    }

    AskDialog .ask-hint {
        margin: 1 0 0 1;
        color: $border;
    }

    AskDialog .ask-nav {
        height: auto;
        margin: 1 0 0 0;
        align: center middle;
    }

    AskDialog .ask-nav-btn {
        margin: 0 1 0 0;
        min-width: 8;
    }
    """

    class Answered(Message):
        """用户回答了问题"""

        def __init__(self, answer: str) -> None:
            super().__init__()
            self.answer = answer

    class TabChanged(Message):
        """用户切换了问题 tab"""

        def __init__(self, header: str) -> None:
            super().__init__()
            self.header = header

    def __init__(self, interrupt_data: dict) -> None:
        super().__init__(classes="ask-dialog")
        self._data = interrupt_data
        self._questions: list[dict] = interrupt_data.get("questions", [])
        self._current_tab: int = 0
        # 每个问题选中的选项索引集合
        self._selected: dict[int, set[int]] = {
            i: set() for i in range(len(self._questions))
        }
        # 每个问题当前高亮的选项索引
        self._highlighted: dict[int, int] = {i: 0 for i in range(len(self._questions))}
        # 每个问题的自定义输入文本
        self._custom_text: dict[int, str] = {i: "" for i in range(len(self._questions))}
        # 是否聚焦在自定义输入框上
        self._input_focused: bool = False

    @property
    def _is_simple(self) -> bool:
        """单问题单选：不显示 Tab 和导航栏"""
        return len(self._questions) == 1 and not self._questions[0].get(
            "multiSelect", False
        )

    def _options_for(self, qi: int) -> list[dict]:
        """获取问题 qi 的有效选项（有 label 的）"""
        return [o for o in self._questions[qi].get("options", []) if o.get("label")]

    def _total_items(self, qi: int) -> int:
        """选项数 + 1（自定义输入行）"""
        return len(self._options_for(qi)) + 1

    def compose(self) -> ComposeResult:
        # Tab 栏
        if not self._is_simple:
            yield Static(self._render_tabs(), id="ask-tabs", classes="ask-tabs")

        # 问题体（全部预先 compose，通过渲染切换可见性）
        for qi, q in enumerate(self._questions):
            yield Static(
                f"[bold {get_color('accent')}][/] {q['question']}",
                id=f"ask-q-{qi}",
                classes="ask-question",
            )
            yield Static(
                self._render_options_list(qi),
                id=f"ask-opts-{qi}",
                classes="ask-options-list",
            )
            # 自定义输入行
            with Horizontal(id=f"ask-custom-{qi}", classes="ask-custom-row"):
                idx = len(self._options_for(qi)) + 1
                yield Static(
                    f"[dim]{idx}. Type something[/]  ",
                    id=f"ask-custom-label-{qi}",
                )
                yield Input(
                    placeholder="输入自定义回答...",
                    id=f"ask-input-{qi}",
                )

        # 操作提示
        yield Static(self._render_hint(), id="ask-hint", classes="ask-hint")

        # 导航栏
        if not self._is_simple:
            yield Horizontal(
                Static("", id="ask-nav-prev", classes="ask-nav-btn"),
                Static("", id="ask-nav-next", classes="ask-nav-btn"),
                id="ask-nav",
                classes="ask-nav",
            )
            self._refresh_nav()

        self._apply_visibility()

    def on_mount(self) -> None:
        self._apply_visibility()
        self.focus()

    # ── 渲染辅助 ──

    def _render_tabs(self) -> str:
        parts = []
        for i, q in enumerate(self._questions):
            header = escape(q.get("header", f"Q{i + 1}"))
            if i == self._current_tab:
                parts.append(f"[bold {get_color('accent')}]「{header}」[/]")
            else:
                parts.append(f"[dim]「{header}」[/]")
        if len(self._questions) > 1:
            if self._current_tab >= len(self._questions):
                parts.append(f"[bold {get_color('success')}]「Submit」[/]")
            else:
                parts.append("[dim]「Submit」[/]")
        return "  ".join(parts)

    def _render_options_list(self, qi: int) -> str:
        opts = self._options_for(qi)
        multi = self._questions[qi].get("multiSelect", False)
        highlighted = self._highlighted.get(qi, 0)
        selected = self._selected.get(qi, set())
        lines = []
        for i, opt in enumerate(opts):
            label = escape(opt["label"])
            desc = escape(opt.get("description", ""))
            suffix = f"  [dim]{desc}[/]" if desc else ""
            num = i + 1
            is_hl = (i == highlighted) and not self._input_focused
            if multi:
                check = "◉" if i in selected else "○"
                prefix = f"{check} {num}."
                if is_hl:
                    lines.append(
                        f"[bold {get_color('accent')}]{prefix} {label}[/]{suffix}"
                    )
                else:
                    lines.append(f"{prefix} {label}{suffix}")
            else:
                prefix = f"{num}."
                if is_hl:
                    lines.append(
                        f"[bold {get_color('accent')}]{prefix} {label}[/]{suffix}"
                    )
                else:
                    lines.append(f"{prefix} {label}{suffix}")
        return "\n".join(lines)

    def _render_hint(self) -> str:
        qi = self._current_tab
        if qi >= len(self._questions):
            return "[dim](enter 提交所有答案, esc 跳过)[/dim]"
        multi = self._questions[qi].get("multiSelect", False)
        nav = "←→ 切题, " if not self._is_simple else ""
        if multi:
            return f"[dim]({nav}↑↓ 移动, 空格 切换, 数字键 快选, tab 输入框, esc 跳过)[/dim]"
        return f"[dim]({nav}↑↓ 移动, enter/数字键 选择, tab 输入框, esc 跳过)[/dim]"

    def _apply_visibility(self) -> None:
        """根据 _current_tab 切换问题体的可见性"""
        for qi in range(len(self._questions)):
            visible = qi == self._current_tab
            for suffix in (f"ask-q-{qi}", f"ask-opts-{qi}", f"ask-custom-{qi}"):
                try:
                    widget = self.query_one(f"#{suffix}")
                    widget.display = visible
                except NoMatches:
                    pass

    def _refresh_current(self, *, tabs_changed: bool = False) -> None:
        """刷新当前 tab 的动态内容"""
        qi = self._current_tab
        if qi < len(self._questions):
            try:
                self.query_one(f"#ask-opts-{qi}", Static).update(
                    self._render_options_list(qi)
                )
            except NoMatches:
                pass
        if tabs_changed:
            if not self._is_simple:
                try:
                    self.query_one("#ask-tabs", Static).update(self._render_tabs())
                except NoMatches:
                    pass
            try:
                self.query_one("#ask-hint", Static).update(self._render_hint())
            except NoMatches:
                pass

    def _refresh_nav(self) -> None:
        """刷新导航栏"""
        if self._is_simple:
            return
        try:
            prev_w = self.query_one("#ask-nav-prev", Static)
            next_w = self.query_one("#ask-nav-next", Static)
            if self._current_tab > 0:
                prev_w.update("[bold][← Prev][/]")
            else:
                prev_w.update("")
            if self._current_tab < len(self._questions) - 1:
                next_w.update("[bold][Next →][/]")
            elif (
                len(self._questions) > 1
                and self._current_tab == len(self._questions) - 1
            ):
                next_w.update(f"[bold {get_color('success')}][Submit][/]")
            else:
                next_w.update("")
        except NoMatches:
            pass

    def _focus_custom_input(self, qi: int) -> None:
        """聚焦到问题 qi 的自定义输入框"""
        self._highlighted[qi] = len(self._options_for(qi))
        self._input_focused = True
        try:
            self.query_one(f"#ask-input-{qi}", Input).focus()
        except NoMatches:
            pass
        self._refresh_current()

    # ── 交互逻辑 ──

    def _switch_tab(self, new_tab: int) -> None:
        if new_tab < 0 or new_tab > len(self._questions):
            return
        # 保存当前自定义输入
        self._save_custom_input()
        self._current_tab = new_tab
        self._input_focused = False
        self._apply_visibility()
        self._refresh_current(tabs_changed=True)
        self._refresh_nav()
        # 通知父组件更新标题
        if new_tab < len(self._questions):
            header = self._questions[new_tab].get("header", "ask")
            self.post_message(self.TabChanged(header))
        # 如果切到 Submit 伪 tab（多问题场景），直接提交
        if new_tab >= len(self._questions):
            self._submit()

    def _save_custom_input(self) -> None:
        qi = self._current_tab
        if qi < len(self._questions):
            try:
                inp = self.query_one(f"#ask-input-{qi}", Input)
                self._custom_text[qi] = inp.value.strip()
            except NoMatches:
                pass

    def _submit(self) -> None:
        self._save_custom_input()
        answer = self._format_answers()
        self.post_message(self.Answered(answer))

    def _decline(self) -> None:
        """用户按 Esc 拒绝回答"""
        self.post_message(self.Answered("User declined to answer questions"))

    def _format_answers(self) -> str:
        parts = []
        for qi, q in enumerate(self._questions):
            opts = self._options_for(qi)
            selected = self._selected.get(qi, set())
            custom = self._custom_text.get(qi, "")

            # 收集选中的 label
            labels = [opts[i]["label"] for i in sorted(selected) if i < len(opts)]
            if custom:
                labels.append(custom)

            answer = ", ".join(labels) if labels else ""
            parts.append(f"{q['question']} → {answer}")
        return "\n".join(parts)

    def on_key(self, event) -> None:
        # 如果 Input 获得焦点，只拦截特定按键
        if self._input_focused:
            if event.key == "escape":
                self._input_focused = False
                self.focus()
                self._refresh_current()
                event.stop()
            elif event.key == "enter":
                self._save_custom_input()
                qi = self._current_tab
                custom = self._custom_text.get(qi, "")
                if custom:
                    multi = self._questions[qi].get("multiSelect", False)
                    if not multi:
                        # 单选 + 自定义输入：直接提交/跳转
                        self._selected[qi] = set()  # 清除已选选项
                        self._advance_or_submit()
                    else:
                        # 多选：回到选项列表
                        self._input_focused = False
                        self.focus()
                        self._refresh_current()
                event.stop()
            return

        qi = self._current_tab
        if qi >= len(self._questions):
            if event.key == "enter":
                self._submit()
                event.stop()
            return

        opts = self._options_for(qi)
        total = self._total_items(qi)
        multi = self._questions[qi].get("multiSelect", False)
        highlighted = self._highlighted.get(qi, 0)

        if event.key == "escape":
            self._decline()
            event.stop()
            return

        if event.key == "left":
            if not self._is_simple and self._current_tab > 0:
                self._switch_tab(self._current_tab - 1)
            event.stop()

        elif event.key == "right":
            if not self._is_simple and self._current_tab < len(self._questions) - 1:
                self._switch_tab(self._current_tab + 1)
            event.stop()

        elif event.key == "up":
            self._highlighted[qi] = (highlighted - 1) % total
            self._input_focused = False
            self._refresh_current()
            event.stop()

        elif event.key == "down":
            self._highlighted[qi] = (highlighted + 1) % total
            self._input_focused = False
            self._refresh_current()
            event.stop()

        elif event.key == "tab":
            self._focus_custom_input(qi)
            event.stop()

        elif event.key == "space" and multi:
            if highlighted < len(opts):
                sel = self._selected[qi]
                if highlighted in sel:
                    sel.discard(highlighted)
                else:
                    sel.add(highlighted)
                self._refresh_current()
            event.stop()

        elif event.key == "enter":
            if multi:
                # 多选模式 Enter → 下一题或提交
                self._advance_or_submit()
            else:
                # 单选模式 Enter → 选中当前并提交/跳转
                if highlighted < len(opts):
                    self._selected[qi] = {highlighted}
                    self._advance_or_submit()
                elif highlighted == len(opts):
                    self._focus_custom_input(qi)
            event.stop()

        elif event.character and event.character.isdigit():
            num = int(event.character)
            if 1 <= num <= len(opts):
                idx = num - 1
                if multi:
                    sel = self._selected[qi]
                    if idx in sel:
                        sel.discard(idx)
                    else:
                        sel.add(idx)
                    self._highlighted[qi] = idx
                    self._refresh_current()
                else:
                    self._selected[qi] = {idx}
                    self._advance_or_submit()
            elif num == len(opts) + 1:
                self._focus_custom_input(qi)
            event.stop()

    def _advance_or_submit(self) -> None:
        """跳转到下一题，或者如果是最后一题则直接提交"""
        if self._current_tab < len(self._questions) - 1:
            self._switch_tab(self._current_tab + 1)
        else:
            self._submit()

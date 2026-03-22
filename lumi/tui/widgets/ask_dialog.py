"""Ask 中断的内联问答组件"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import DescendantBlur, DescendantFocus, Key
from textual.message import Message
from textual.widgets import Input, Static

from lumi.tui.renderers.utils import escape_markup as escape
from lumi.tui.theme import get_color
from lumi.utils.logger import logger


class AskDialog(Vertical):
    """Ask 中断的内联问答组件

    支持：
    - 多问题 Tab 导航
    - 单选（选中即确认）与多选（checkbox）模式
    - 自定义文本输入（与选项视觉统一，激活后展开 Input）
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
    AskDialog .ask-tabs { height: auto; margin: 0 0 1 0; }
    AskDialog .ask-question {
        text-style: bold; margin: 0 0 1 0; color: $accent;
    }
    AskDialog .ask-options-list { height: auto; margin: 0 0 0 1; }
    AskDialog .ask-input-row { height: 1; margin: 0 0 0 1; }
    AskDialog .ask-input-prefix { width: auto; height: 1; }
    AskDialog .ask-custom-input {
        height: 1; border: none; padding: 0;
        background: transparent; color: $foreground;
    }
    AskDialog .ask-custom-input:focus { border: none; padding: 0; }
    AskDialog .ask-hint { margin: 1 0 0 1; color: $border; }
    AskDialog .ask-nav { height: auto; margin: 1 0 0 0; align: center middle; }
    AskDialog .ask-nav-btn { margin: 0 1 0 0; min-width: 8; }
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
        self._questions: list[dict] = interrupt_data.get("questions", [])
        n = len(self._questions)
        self._current_tab: int = 0
        self._selected: dict[int, set[int]] = {i: set() for i in range(n)}
        self._highlighted: dict[int, int] = {i: 0 for i in range(n)}
        self._custom_text: dict[int, str] = {i: "" for i in range(n)}
        self._input_focused: bool = False
        # 缓存每个问题的有效选项列表（有 label 的）
        self._opts_cache: dict[int, list[dict]] = {
            i: [o for o in q.get("options", []) if o.get("label")]
            for i, q in enumerate(self._questions)
        }

    # ── 属性与查询辅助 ──

    @property
    def _submit_tab_index(self) -> int:
        """Submit 伪 tab 的索引（最后一题之后）"""
        return len(self._questions)

    @property
    def _is_simple(self) -> bool:
        """单问题单选：不显示 Tab 和导航栏"""
        return len(self._questions) == 1 and not self._questions[0].get(
            "multiSelect", False
        )

    def _opts(self, qi: int) -> list[dict]:
        """获取问题 qi 的有效选项（缓存）"""
        return self._opts_cache[qi]

    def _is_multi(self, qi: int) -> bool:
        """问题 qi 是否为多选"""
        return self._questions[qi].get("multiSelect", False)

    def _total_items(self, qi: int) -> int:
        """选项数 + 1（自定义输入行）"""
        return len(self._opts(qi)) + 1

    def _query_static(self, selector: str) -> Static | None:
        """安全查询 Static 组件，未找到返回 None"""
        try:
            return self.query_one(selector, Static)
        except NoMatches:
            logger.debug("Widget not found: %s", selector)
            return None

    def _set_display(self, selector: str, visible: bool) -> None:
        """安全设置组件 display 属性"""
        try:
            self.query_one(selector).display = visible
        except NoMatches:
            logger.debug("Widget not found for display update: %s", selector)

    # ── 组合 ──

    def compose(self) -> ComposeResult:
        if not self._is_simple:
            yield Static(self._render_tabs(), id="ask-tabs", classes="ask-tabs")

        for qi, q in enumerate(self._questions):
            yield Static(
                f"[bold {get_color('accent')}][/] {escape(q['question'])}",
                id=f"ask-q-{qi}",
                classes="ask-question",
            )
            yield Static(
                self._render_options_list(qi),
                id=f"ask-opts-{qi}",
                classes="ask-options-list",
            )
            # 输入行：默认隐藏，激活时替代选项列表中的最后一行
            with Horizontal(id=f"ask-input-row-{qi}", classes="ask-input-row"):
                yield Static(
                    self._build_input_prefix(qi),
                    id=f"ask-input-prefix-{qi}",
                    classes="ask-input-prefix",
                )
                yield Input(
                    placeholder="Type something",
                    id=f"ask-input-{qi}",
                    classes="ask-custom-input",
                )

        # Submit 确认页摘要（多问题时显示）
        if not self._is_simple:
            yield Static("", id="ask-submit-summary", classes="ask-options-list")

        yield Static(self._render_hint(), id="ask-hint", classes="ask-hint")

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

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        """鼠标点击 Input 时同步状态"""
        if not self._is_ask_input(event.widget):
            return
        qi = self._current_tab
        if not self._input_focused:
            self._highlighted[qi] = len(self._opts(qi))
            self._activate_input(qi)

    def on_descendant_blur(self, event: DescendantBlur) -> None:
        """Input 失去焦点时同步状态"""
        if not self._is_ask_input(event.widget):
            return
        if self._input_focused:
            self._deactivate_input()

    @staticmethod
    def _is_ask_input(widget) -> bool:
        """判断 widget 是否为本组件的 Input"""
        return isinstance(widget, Input) and (widget.id or "").startswith("ask-input-")

    # ── 渲染辅助 ──

    def _build_custom_prefix(self, qi: int) -> str:
        """构建自定义输入项的 prefix（序号 + 多选 checkbox）"""
        idx = len(self._opts(qi)) + 1
        if self._is_multi(qi):
            check = "◉" if self._custom_text.get(qi, "") else "○"
            return f"{check} {idx}."
        return f"{idx}."

    def _build_input_prefix(self, qi: int) -> str:
        """构建 Input 行的 prefix markup"""
        idx = len(self._opts(qi)) + 1
        if self._is_multi(qi):
            check = "◉" if self._custom_text.get(qi, "") else "○"
            return f"{check} [dim]{idx}.[/] "
        return f"[dim]{idx}.[/] "

    def _render_tabs(self) -> str:
        """渲染 Tab 栏"""
        accent = get_color("accent")
        parts = []
        for i, q in enumerate(self._questions):
            header = escape(q.get("header", f"Q{i + 1}"))
            if i == self._current_tab:
                parts.append(f"[bold {accent}]「{header}」[/]")
            else:
                parts.append(f"[dim]「{header}」[/]")
        if len(self._questions) > 1:
            if self._current_tab >= self._submit_tab_index:
                parts.append(f"[bold {get_color('success')}]「Submit」[/]")
            else:
                parts.append("[dim]「Submit」[/]")
        return "  ".join(parts)

    def _render_options_list(self, qi: int) -> str:
        """渲染选项列表（含自定义输入项作为最后一行）"""
        opts = self._opts(qi)
        multi = self._is_multi(qi)
        highlighted = self._highlighted.get(qi, 0)
        selected = self._selected.get(qi, set())
        accent = get_color("accent")
        lines: list[str] = []

        for i, opt in enumerate(opts):
            label = escape(opt["label"])
            desc = escape(opt.get("description", ""))
            suffix = f"  [dim]{desc}[/]" if desc else ""
            num = i + 1
            is_hl = (i == highlighted) and not self._input_focused
            if multi:
                check = "◉" if i in selected else "○"
                prefix = f"{check} {num}."
            else:
                prefix = f"{num}."
            if is_hl:
                lines.append(f"[bold {accent}]{prefix} {label}[/]{suffix}")
            else:
                lines.append(f"{prefix} {label}{suffix}")

        # 自定义输入项：Input 未激活时显示为普通选项行
        if not self._input_focused:
            prefix = self._build_custom_prefix(qi)
            custom = self._custom_text.get(qi, "")
            is_hl = highlighted == len(opts)
            label = escape(custom) if custom else "[dim]Type something[/]"
            if is_hl:
                if custom:
                    lines.append(f"[bold {accent}]{prefix} {label}[/]")
                else:
                    lines.append(f"[bold {accent}]{prefix}[/] {label}")
            else:
                lines.append(f"{prefix} {label}")

        return "\n".join(lines)

    def _render_hint(self) -> str:
        """渲染操作提示"""
        qi = self._current_tab
        if qi >= len(self._questions):
            return "[dim](enter 提交, ← 返回修改, esc 中断)[/dim]"
        nav = "←→ 切题, " if not self._is_simple else ""
        if self._is_multi(qi):
            return f"[dim]({nav}↑↓ 移动, 空格 选择, 数字键 快选, esc 中断)[/dim]"
        return f"[dim]({nav}↑↓ 移动, enter/数字键 选择, esc 中断)[/dim]"

    def _render_submit_summary(self) -> str:
        """渲染 Submit 确认页的答案摘要"""
        accent = get_color("accent")
        lines: list[str] = []
        for qi, q in enumerate(self._questions):
            opts = self._opts(qi)
            selected = self._selected.get(qi, set())
            custom = self._custom_text.get(qi, "")
            labels = [opts[i]["label"] for i in sorted(selected) if i < len(opts)]
            if custom and (self._is_multi(qi) or not labels):
                labels.append(custom)
            answer = ", ".join(labels) if labels else "[dim]未选择[/]"
            header = escape(q.get("header", f"Q{qi + 1}"))
            lines.append(f"[bold {accent}]{header}[/]: {answer}")
        return "\n".join(lines)

    # ── 可见性与刷新 ──

    def _apply_visibility(self) -> None:
        """根据 _current_tab 切换问题体的可见性"""
        on_submit = self._current_tab >= self._submit_tab_index
        for qi in range(len(self._questions)):
            visible = qi == self._current_tab
            self._set_display(f"#ask-q-{qi}", visible)
            self._set_display(f"#ask-opts-{qi}", visible)
            self._set_display(f"#ask-input-row-{qi}", visible and self._input_focused)
        # Submit 确认页摘要
        if not self._is_simple:
            self._set_display("#ask-submit-summary", on_submit)

    def _refresh_current(self, *, tabs_changed: bool = False) -> None:
        """刷新当前 tab 的动态内容"""
        qi = self._current_tab
        if qi < len(self._questions):
            w = self._query_static(f"#ask-opts-{qi}")
            if w:
                w.update(self._render_options_list(qi))
        elif not self._is_simple:
            # Submit 确认页：刷新摘要
            w = self._query_static("#ask-submit-summary")
            if w:
                w.update(self._render_submit_summary())
        if tabs_changed:
            if not self._is_simple:
                w = self._query_static("#ask-tabs")
                if w:
                    w.update(self._render_tabs())
            w = self._query_static("#ask-hint")
            if w:
                w.update(self._render_hint())

    def _refresh_nav(self) -> None:
        """刷新导航栏"""
        if self._is_simple:
            return
        prev_w = self._query_static("#ask-nav-prev")
        next_w = self._query_static("#ask-nav-next")
        if not prev_w or not next_w:
            return
        prev_w.update("[bold][← Prev][/]" if self._current_tab > 0 else "")
        if self._current_tab < len(self._questions) - 1:
            next_w.update("[bold][Next →][/]")
        elif len(self._questions) > 1:
            next_w.update(f"[bold {get_color('success')}][Submit][/]")
        else:
            next_w.update("")

    # ── 输入框激活/退出 ──

    def _activate_input(self, qi: int) -> None:
        """激活自定义输入框"""
        self._highlighted[qi] = len(self._opts(qi))
        self._input_focused = True
        self._set_display(f"#ask-input-row-{qi}", True)
        # 刷新 prefix（多选时含 checkbox）
        w = self._query_static(f"#ask-input-prefix-{qi}")
        if w:
            w.update(self._build_input_prefix(qi))
        self._refresh_current()
        try:
            self.query_one(f"#ask-input-{qi}", Input).focus()
        except NoMatches:
            logger.warning("Input widget not found for focus: q%d", qi)
            # 回滚状态，避免用户卡在无法输入的幽灵模式
            self._input_focused = False
            self._set_display(f"#ask-input-row-{qi}", False)
            self._refresh_current()

    def _deactivate_input(self) -> None:
        """退出自定义输入框，恢复焦点到 Dialog"""
        self._save_custom_input()
        qi = self._current_tab
        self._input_focused = False
        self.focus()
        self._set_display(f"#ask-input-row-{qi}", False)
        self._refresh_current()

    def _save_custom_input(self) -> None:
        """保存当前 Input 的值"""
        qi = self._current_tab
        if qi < len(self._questions):
            try:
                inp = self.query_one(f"#ask-input-{qi}", Input)
                self._custom_text[qi] = inp.value.strip()
            except NoMatches:
                logger.warning("Input widget not found, custom input lost: q%d", qi)

    # ── 交互逻辑 ──

    def _switch_tab(self, new_tab: int) -> None:
        """切换到指定 tab"""
        if new_tab < 0 or new_tab > self._submit_tab_index:
            return
        self._save_custom_input()
        self._current_tab = new_tab
        self._input_focused = False
        self._apply_visibility()
        self._refresh_current(tabs_changed=True)
        self._refresh_nav()
        if new_tab < len(self._questions):
            header = self._questions[new_tab].get("header", "ask")
            self.post_message(self.TabChanged(header))

    def _submit(self) -> None:
        """提交所有答案"""
        self._save_custom_input()
        self.post_message(self.Answered(self._format_answers()))

    def _decline(self) -> None:
        """用户按 Esc 拒绝回答"""
        from lumi.agents.tools.providers.ask import ASK_CANCELLED

        self.post_message(self.Answered(ASK_CANCELLED))

    def _advance_or_submit(self) -> None:
        """跳转到下一题；多问题时最后一题跳到 Submit 确认页，单问题直接提交"""
        if self._current_tab < len(self._questions) - 1:
            self._switch_tab(self._current_tab + 1)
        elif self._is_simple:
            self._submit()
        else:
            self._switch_tab(len(self._questions))

    def _format_answers(self) -> str:
        """格式化所有问题的答案"""
        parts: list[str] = []
        for qi, q in enumerate(self._questions):
            opts = self._opts(qi)
            selected = self._selected.get(qi, set())
            custom = self._custom_text.get(qi, "")
            multi = q.get("multiSelect", False)

            labels = [opts[i]["label"] for i in sorted(selected) if i < len(opts)]
            if custom and (multi or not labels):
                labels.append(custom)

            answer = ", ".join(labels) if labels else ""
            parts.append(f"{q['question']} → {answer}")
        return "\n".join(parts)

    # ── 键盘事件 ──

    def on_key(self, event: Key) -> None:
        """键盘事件分发"""
        if self._input_focused:
            self._handle_input_key(event)
            return
        qi = self._current_tab
        if qi >= len(self._questions):
            self._handle_submit_key(event)
            return
        self._handle_option_key(event, qi)

    def _handle_input_key(self, event: Key) -> None:
        """输入框激活时的按键处理"""
        if event.key in ("escape", "enter"):
            self._deactivate_input()
            event.stop()

    def _handle_submit_key(self, event: Key) -> None:
        """Submit 确认页的按键处理"""
        match event.key:
            case "enter":
                self._submit()
                event.stop()
            case "escape":
                self._decline()
                event.stop()
            case "left":
                self._switch_tab(self._submit_tab_index - 1)
                event.stop()

    def _handle_option_key(self, event: Key, qi: int) -> None:
        """选项列表的按键处理"""
        opts = self._opts(qi)
        total = self._total_items(qi)
        multi = self._is_multi(qi)
        highlighted = self._highlighted.get(qi, 0)

        match event.key:
            case "escape":
                self._decline()
                event.stop()
            case "left":
                if not self._is_simple and self._current_tab > 0:
                    self._switch_tab(self._current_tab - 1)
                event.stop()
            case "right":
                if not self._is_simple and self._current_tab < self._submit_tab_index:
                    self._switch_tab(self._current_tab + 1)
                event.stop()
            case "up":
                self._highlighted[qi] = (highlighted - 1) % total
                self._refresh_current()
                event.stop()
            case "down":
                self._highlighted[qi] = (highlighted + 1) % total
                self._refresh_current()
                event.stop()
            case "tab":
                event.stop()
            case "space" if multi:
                if highlighted < len(opts):
                    sel = self._selected[qi]
                    sel.symmetric_difference_update({highlighted})
                    self._refresh_current()
                event.stop()
            case "enter":
                self._handle_enter(qi, opts, highlighted, multi)
                event.stop()
            case _ if event.character and event.character.isdigit():
                self._handle_digit(qi, opts, int(event.character), multi)
                event.stop()

    def _handle_enter(
        self, qi: int, opts: list[dict], highlighted: int, multi: bool
    ) -> None:
        """Enter 键处理：选中选项或激活自定义输入"""
        if highlighted == len(opts):
            # 自定义输入项
            custom = self._custom_text.get(qi, "")
            if custom:
                # 已有文本，清空选中直接提交
                self._selected[qi] = set()
                self._advance_or_submit()
            else:
                self._activate_input(qi)
        elif multi:
            self._advance_or_submit()
        else:
            self._selected[qi] = {highlighted}
            self._custom_text[qi] = ""
            self._advance_or_submit()

    def _handle_digit(self, qi: int, opts: list[dict], num: int, multi: bool) -> None:
        """数字键处理：快速选择选项或激活自定义输入"""
        if 1 <= num <= len(opts):
            idx = num - 1
            if multi:
                self._selected[qi].symmetric_difference_update({idx})
                self._highlighted[qi] = idx
                self._refresh_current()
            else:
                self._selected[qi] = {idx}
                self._advance_or_submit()
        elif num == len(opts) + 1:
            self._activate_input(qi)

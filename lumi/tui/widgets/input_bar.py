"""底部输入栏"""

from __future__ import annotations

import re

from rich.style import Style
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key, Paste
from textual.message import Message
from textual.widgets import Static, TextArea

from lumi.tui.slash_commands.parser import extract_command_prefix, is_command_mode
from lumi.tui.slash_commands.registry import CommandRegistry
from lumi.tui.theme import get_color
from lumi.tui.widgets.completion_menu import CompletionMenu
from lumi.utils.image import ImageData

# 粘贴内容超过此行数时折叠显示
PASTE_COLLAPSE_THRESHOLD = 20

# 值为 (label, 颜色)
_MODE_DISPLAY: dict[str, tuple[str, str]] = {
    "auto": ("▶ auto", "#88E8A0"),
    "plan": ("⏸ plan", "#E8D888"),
    "privileged": ("▶▶ privileged ⚠", "#88A0E8"),
}


class ChatInput(TextArea):
    """聊天输入框 - 基于 TextArea，支持自动换行和粘贴折叠。

    Enter 提交，Shift+Enter 换行。粘贴超长内容时自动折叠。
    """

    DEFAULT_CSS = """
    ChatInput {
        background: transparent;
        border: none !important;
        width: 1fr;
        height: auto;
        min-height: 1;
        max-height: 8;
        padding: 0;
        margin: 0;
        color: $foreground;
    }

    ChatInput:focus {
        border: none !important;
    }

    ChatInput > .text-area--cursor-line {
        background: transparent;
    }
    """

    class Submitted(Message):
        """用户按 Enter 提交"""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        super().__init__(
            id="user-input",
            show_line_numbers=False,
            soft_wrap=True,
            tab_behavior="focus",
            **kwargs,
        )
        # 粘贴计数器和原始文本存储
        self._paste_counter: int = 0
        self._pasted_texts: dict[int, str] = {}
        # 斜杠命令高亮
        self._command_registry: CommandRegistry | None = None
        self._cmd_highlight_end: int = 0
        # 注册自定义 theme，包含 slash_command 样式
        from textual._text_area_theme import TextAreaTheme

        theme = TextAreaTheme(
            name="lumi-input",
            syntax_styles={"slash_command": Style(color="#7eb6ff", bold=True)},
        )
        self.register_theme(theme)
        self.theme = "lumi-input"

    @property
    def value(self) -> str:
        """兼容旧 Input.value 接口，返回实际文本（展开折叠内容）。"""
        text = self.text
        # 展开所有折叠标记为原始文本
        for idx, original in self._pasted_texts.items():
            tag = f"[Pasted text #{idx}"
            if tag in text:
                pattern = rf"\[Pasted text #{idx} \+\d+ lines\]"
                text = re.sub(pattern, original, text)
        return text

    @value.setter
    def value(self, new_value: str) -> None:
        """兼容旧 Input.value = '' 接口。"""
        self._pasted_texts.clear()
        self.clear()
        if new_value:
            self.insert(new_value)

    async def _on_key(self, event: Key) -> None:
        """Enter 提交，Shift+Enter 换行。

        Textual 键盘事件流：聚焦组件(ChatInput) → 冒泡到父容器(InputBar)。
        补全菜单的确认逻辑统一在 InputBar.on_key 中处理，因此这里需要：
        - 菜单可见时：prevent_default 阻止 TextArea 插入换行，
          但 **不调用 stop()** 让事件冒泡到 InputBar 完成补全确认。
        - 菜单不可见时：prevent_default + stop 拦截事件，作为消息提交。
        """
        if event.key == "enter":
            # 补全菜单可见 → 阻止换行，放行冒泡给 InputBar 处理补全
            menu = self.screen.query_one(CompletionMenu)
            if menu.is_visible:
                event.prevent_default()
                return
            # 普通提交 → 阻止换行并停止冒泡
            event.prevent_default()
            event.stop()
            text = self.value.strip()
            if text:
                self.post_message(self.Submitted(text))
        # shift+enter 由 TextArea 默认处理为换行，无需干预

    def _on_paste(self, event: Paste) -> None:
        """拦截粘贴事件，超长内容折叠显示。"""
        pasted = event.text
        if not pasted:
            return

        lines = pasted.splitlines()
        line_count = len(lines)

        if line_count > PASTE_COLLAPSE_THRESHOLD:
            event.prevent_default()
            event.stop()
            self._paste_counter += 1
            idx = self._paste_counter
            # 保存原始文本
            self._pasted_texts[idx] = pasted
            # 在输入框中显示折叠标记
            collapse_tag = f"[Pasted text #{idx} +{line_count} lines]"
            self.insert(collapse_tag)
        # 短内容正常粘贴，TextArea 自动换行

    def set_command_registry(self, registry: CommandRegistry) -> None:
        """注入命令注册表，用于斜杠命令高亮。"""
        self._command_registry = registry

    def update_command_highlight(self) -> None:
        """根据当前文本更新斜杠命令高亮范围。

        输入含空格/换行时要求精确匹配命令名，否则按前缀模糊匹配。
        """
        text = self.text
        prefix = extract_command_prefix(text) if text.startswith("/") else ""

        if prefix and self._command_registry:
            has_space = " " in text or "\n" in text
            matched = (
                self._command_registry.get(prefix) is not None
                if has_space
                else len(self._command_registry.match(prefix)) > 0
            )
            self._cmd_highlight_end = 1 + len(prefix) if matched else 0
        else:
            self._cmd_highlight_end = 0

        self._apply_cmd_highlight()

    def _apply_cmd_highlight(self) -> None:
        """将当前命令高亮写入第 0 行的 _highlights（斜杠命令仅出现在首行）并刷新渲染缓存。"""
        end = self._cmd_highlight_end
        self._highlights[0] = [(0, end, "slash_command")] if end > 0 else []
        self._line_cache.clear()

    def _build_highlight_map(self) -> None:
        """Override: 在基类高亮重建后，重新注入斜杠命令高亮。"""
        super()._build_highlight_map()
        self._apply_cmd_highlight()


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
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="input-row"):
            yield Static("> ", id="prompt-label")
            yield ChatInput()


class InputBar(Vertical):
    """底部输入栏 - 带 > 提示符 + 模式指示器"""

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        max-height: 14;
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

        def __init__(
            self,
            text: str,
            tool_mode: str,
            plan_mode: bool = False,
            plan_reminder_pending: bool = False,
            images: list[ImageData] | None = None,
        ) -> None:
            super().__init__()
            self.text = text
            self.tool_mode = tool_mode
            self.plan_mode = plan_mode
            self.plan_reminder_pending = plan_reminder_pending
            self.images: list[ImageData] = images or []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tool_mode = "auto"
        self._plan_mode = False
        self._plan_reminder_pending = False
        self._privileged = False
        self._pending_images: list[ImageData] = []
        self._exit_hint_timer = None
        self._flash_timer = None
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""  # 暂存当前未提交的输入
        self._command_registry: CommandRegistry | None = None
        self._submit_disabled: bool = False

    def set_command_registry(self, registry: CommandRegistry) -> None:
        """允许外部（如 LumiApp）注入命令注册表。"""
        self._command_registry = registry
        self.query_one("#user-input", ChatInput).set_command_registry(registry)

    def compose(self) -> ComposeResult:
        yield InputBox()
        yield CompletionMenu()
        display_key = self._current_display_key()
        label, color = _MODE_DISPLAY[display_key]
        hint = " [dim](shift+tab)[/dim]"
        with Horizontal(id="status-row"):
            yield Static(
                f"[{color}]{label}[/]{hint}",
                id="mode-indicator",
            )
            yield Static("[#B888E8]⚑[/]", id="bell-indicator")

    def on_mount(self) -> None:
        self.query_one(InputBox).border_title = "Input"
        self.query_one("#user-input", ChatInput).focus()

    def on_key(self, event: Key) -> None:
        # 补全菜单可见时，拦截导航和确认键
        menu = self.query_one(CompletionMenu)
        if menu.is_visible and event.key in ("up", "down", "enter", "tab", "escape"):
            event.prevent_default()
            event.stop()
            if event.key == "up":
                menu.move_selection(-1)
            elif event.key == "down":
                menu.move_selection(1)
            elif event.key in ("enter", "tab"):
                menu.confirm_selection()
            else:  # escape
                menu.hide()
            return

        if event.key == "ctrl+v":
            event.prevent_default()
            event.stop()
            self._try_paste_image()
        elif event.key == "shift+tab":
            event.prevent_default()
            event.stop()
            self.action_toggle_plan_mode()
        elif event.key == "up":
            inp = self.query_one("#user-input", ChatInput)
            # 只在光标在第一行时才浏览历史
            row, _col = inp.cursor_location
            if row == 0:
                event.prevent_default()
                event.stop()
                self._navigate_history(-1)
        elif event.key == "down":
            inp = self.query_one("#user-input", ChatInput)
            # 只在光标在最后一行时才浏览历史
            row, _col = inp.cursor_location
            last_row = inp.document.line_count - 1
            if row >= last_row:
                event.prevent_default()
                event.stop()
                self._navigate_history(1)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """输入内容变化时隐藏退出提示，并检测命令模式。

        仅在用户正在输入命令前缀时（无空格）展示补全菜单，
        输入空格后表示命令名已确定，隐藏菜单。
        """
        self.hide_exit_hint()
        text = event.text_area.text
        menu = self.query_one(CompletionMenu)
        # 命令模式：以 / 开头且尚未输入空格（仍在输入命令名）
        if self._command_registry and is_command_mode(text) and " " not in text:
            prefix = extract_command_prefix(text)
            matched = self._command_registry.match(prefix)
            menu.show_commands(matched)
        else:
            menu.hide()
        # 更新斜杠命令高亮
        inp = self.query_one("#user-input", ChatInput)
        inp.update_command_highlight()

    def on_completion_menu_command_selected(
        self, event: CompletionMenu.CommandSelected
    ) -> None:
        """补全菜单选中命令后，填入输入框。"""
        inp = self.query_one("#user-input", ChatInput)
        inp.value = f"/{event.command_name} "
        inp.move_cursor(inp.document.end)
        self.query_one(CompletionMenu).hide()
        inp.update_command_highlight()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """ChatInput Enter 提交"""
        if self._submit_disabled:
            return
        text = event.value.strip()
        if text:
            self._history.append(text)
            self._history_index = -1
            self._draft = ""
            inp = self.query_one("#user-input", ChatInput)
            inp.value = ""
            images = self._pending_images.copy()
            self._pending_images.clear()
            self._update_image_indicator()
            pending = self._plan_reminder_pending
            self.post_message(
                self.Submitted(
                    text,
                    self._tool_mode,
                    plan_mode=self._plan_mode,
                    plan_reminder_pending=pending,
                    images=images,
                )
            )

    def _navigate_history(self, direction: int) -> None:
        """上下键浏览输入历史

        Args:
            direction: -1 向上（更早），1 向下（更近）
        """
        if not self._history:
            return
        inp = self.query_one("#user-input", ChatInput)
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
        inp.move_cursor(inp.document.end)

    def _try_paste_image(self) -> None:
        """尝试从剪贴板粘贴图片（异步）。"""
        from lumi.utils.clipboard import read_image_from_clipboard

        async def _do_paste() -> None:
            image = await read_image_from_clipboard()
            if image is not None:
                self._pending_images.append(image)
                self._update_image_indicator()
                self.hide_exit_hint()

        self.run_worker(_do_paste(), exclusive=False)

    def _update_image_indicator(self) -> None:
        """更新输入框标题以反映图片附件状态。"""
        box = self.query_one(InputBox)
        count = len(self._pending_images)
        if count > 0:
            box.border_title = f"Input [{count} 张图片]"
        else:
            box.border_title = "Input"

    def action_toggle_plan_mode(self) -> None:
        """切换 plan mode"""
        self._plan_mode = not self._plan_mode
        self._plan_reminder_pending = self._plan_mode
        self._update_mode_indicator()

    def _current_display_key(self) -> str:
        """根据当前状态返回 _MODE_DISPLAY 的 key。"""
        if self._plan_mode:
            return "plan"
        return "privileged" if self._privileged else "auto"

    def _update_mode_indicator(self) -> None:
        display_key = self._current_display_key()
        label, color = _MODE_DISPLAY[display_key]
        hint = " [dim](shift+tab)[/dim]"
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[{color}]{label}[/]{hint}")

    def flash_message(self, message: str, duration: float = 1.5) -> None:
        """在状态栏短暂显示提示消息，之后恢复原内容。

        Args:
            message: 提示文本（如 "Copied"）。
            duration: 显示时长（秒）。
        """
        # 取消可能冲突的计时器
        if self._flash_timer is not None:
            self._flash_timer.stop()
        if self._exit_hint_timer is not None:
            self._exit_hint_timer.stop()
            self._exit_hint_timer = None
        color = get_color("success")
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[{color}]✓ {message}[/]")
        self._flash_timer = self.set_timer(duration, self._restore_from_flash)

    def _restore_from_flash(self) -> None:
        """flash_message 计时器回调：恢复状态栏。"""
        self._flash_timer = None
        self._update_mode_indicator()

    @property
    def tool_mode(self) -> str:
        """获取当前 tool_mode"""
        return self._tool_mode

    @property
    def plan_mode(self) -> bool:
        """获取当前 plan_mode"""
        return self._plan_mode

    @property
    def plan_reminder_pending(self) -> bool:
        """是否有待注入的 plan reminder"""
        return self._plan_reminder_pending

    def consume_plan_reminder(self) -> None:
        """标记 plan reminder 已注入，后续消息不再重复注入"""
        self._plan_reminder_pending = False

    def set_plan_mode(self, on: bool, *, reminder_pending: bool = True) -> None:
        """外部设置 plan mode 并更新指示器。

        Args:
            on: 是否开启 plan mode
            reminder_pending: 是否需要在下一条消息注入 reminder。
                LLM 调用 EnterPlanMode 时设为 False（tool response 已含 reminder）。
        """
        self._plan_mode = on
        self._plan_reminder_pending = on and reminder_pending
        self._update_mode_indicator()

    def set_privileged(self, on: bool) -> None:
        """设置 privileged 模式（仅启动时通过 CLI flag 设置）"""
        self._privileged = on
        self._tool_mode = "privileged" if on else "auto"
        self._update_mode_indicator()

    def show_exit_hint(self) -> None:
        """在状态栏显示退出提示，1.5 秒后自动恢复。"""
        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer = None
        if self._exit_hint_timer is not None:
            self._exit_hint_timer.stop()
        color = get_color("error")
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[{color}]Double press ctrl+c to exit[/]")
        self._exit_hint_timer = self.set_timer(1.5, self.hide_exit_hint)

    def hide_exit_hint(self) -> None:
        """恢复状态栏原内容并取消计时器。"""
        if self._exit_hint_timer is not None:
            self._exit_hint_timer.stop()
            self._exit_hint_timer = None
        self._update_mode_indicator()

    @property
    def has_pending_images(self) -> bool:
        return bool(self._pending_images)

    def clear_images(self) -> None:
        """清空所有待发送图片。"""
        self._pending_images.clear()
        self._update_image_indicator()

    def set_disabled(self, disabled: bool) -> None:
        """禁用/启用输入提交（输入框始终保持可编辑，避免 Textual 渲染黑框）"""
        self._submit_disabled = disabled
        if not disabled:
            inp = self.query_one("#user-input", ChatInput)
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

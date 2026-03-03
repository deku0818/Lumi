"""首次启动初始化引导界面

当 initialized 为 False 时，TUI 启动后弹出此 ModalScreen，
引导用户完成基本设置（如主题模式选择）。
用户无法通过 escape 键退出，必须完成引导流程。
ctrl+c 可退出程序。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Label, RadioButton, RadioSet, Rule, Static

from lumi.utils.config import GlobalConfig, GlobalConfigManager

# 主题模式选项映射：显示文本 → 配置值
_THEME_OPTIONS: list[tuple[str, str]] = [
    ("● 暗色 (Dark)", "dark"),
    ("○ 明亮 (Light)", "light"),
    ("◐ 跟随系统 (System)", "system"),
]

_DEFAULT_THEME_MODE = "system"


class InitFlowScreen(ModalScreen[GlobalConfig]):
    """首次启动初始化引导界面

    继承 ModalScreen 以阻止 escape 退出。
    引导用户选择主题模式，完成后保存配置并返回 GlobalConfig。
    支持后续扩展更多初始化步骤。
    """

    BINDINGS = [("ctrl+c", "quit_app", "Quit")]

    DEFAULT_CSS = """
    InitFlowScreen {
        align: center middle;
    }

    InitFlowScreen > Vertical {
        width: 56;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }

    InitFlowScreen .welcome {
        text-align: center;
        text-style: bold;
        color: $accent;
        width: 100%;
    }

    InitFlowScreen .description {
        text-align: center;
        width: 100%;
        color: $text-muted;
        margin-bottom: 1;
    }

    InitFlowScreen .section-label {
        text-style: bold;
        margin-bottom: 0;
    }

    InitFlowScreen RadioSet {
        width: 100%;
        background: transparent;
        border: none;
        margin-bottom: 1;
    }

    InitFlowScreen .hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        """渲染初始化引导界面。"""
        default_index = next(
            i for i, (_, v) in enumerate(_THEME_OPTIONS) if v == _DEFAULT_THEME_MODE
        )
        container = Vertical()
        container.border_title = "Lumi Setup"
        with container:
            yield Label("» 欢迎使用 Lumi", classes="welcome")
            yield Static("首次启动，请完成以下初始设置", classes="description")
            yield Rule()
            yield Label("主题模式", classes="section-label")
            with RadioSet(id="theme-mode"):
                for i, (label, _value) in enumerate(_THEME_OPTIONS):
                    yield RadioButton(label, value=i == default_index)
            yield Rule()
            yield Static(
                "[dim]↑↓ 选择  ·  Enter 确认  ·  ctrl+c 退出[/]",
                classes="hint",
            )

    def _on_key(self, event: Key) -> None:
        """拦截 escape 键，阻止退出引导流程。"""
        if event.key == "escape":
            event.prevent_default()
            event.stop()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """用户切换选项后自动确认保存。"""
        # 不在切换时自动保存，等用户按 enter
        pass

    def key_enter(self) -> None:
        """按 Enter 确认当前选择。"""
        self._confirm()

    def _confirm(self) -> None:
        """保存用户选择的配置并关闭引导界面。"""
        radio_set = self.query_one("#theme-mode", RadioSet)
        pressed_index = radio_set.pressed_index
        if pressed_index < 0:
            pressed_index = next(
                i for i, (_, v) in enumerate(_THEME_OPTIONS) if v == _DEFAULT_THEME_MODE
            )

        _, selected_value = _THEME_OPTIONS[pressed_index]
        config = GlobalConfig(initialized=True, theme_mode=selected_value)
        GlobalConfigManager.save(config)
        self.dismiss(config)

    async def action_quit_app(self) -> None:
        """ctrl+c 退出程序。"""
        self.app.exit()

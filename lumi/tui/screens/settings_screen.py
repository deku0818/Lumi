"""TUI 设置界面

提供交互式全局配置修改界面，支持主题模式选择。
完全键盘操作：↑↓ 选择，Enter 保存，Escape 取消。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, RadioButton, RadioSet, Rule, Static

from lumi.utils.config import GlobalConfig, GlobalConfigManager

from ._constants import THEME_OPTIONS as _THEME_OPTIONS


class SettingsScreen(ModalScreen[GlobalConfig | None]):
    """TUI 设置界面

    显示当前全局配置并允许用户修改主题模式。
    Enter 保存并关闭，Escape 取消并关闭。
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("ctrl+c", "quit_app", "Quit"),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }

    SettingsScreen > Vertical {
        width: 50;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }

    SettingsScreen .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        width: 100%;
    }

    SettingsScreen .section-label {
        text-style: bold;
        margin-bottom: 0;
    }

    SettingsScreen RadioSet {
        width: 100%;
        background: transparent;
        border: none;
        margin-bottom: 1;
    }

    SettingsScreen .hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def __init__(self, config: GlobalConfig) -> None:
        """初始化设置界面。

        Args:
            config: 当前全局配置实例，用于显示当前值。
        """
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        """渲染设置界面。"""
        container = Vertical()
        container.border_title = "Settings"
        with container:
            yield Label("» 设置", classes="title")
            yield Rule()
            yield Label("主题模式", classes="section-label")
            with RadioSet(id="theme-mode"):
                for label, value in _THEME_OPTIONS:
                    yield RadioButton(
                        label,
                        value=value == self._config.theme_mode,
                    )
            yield Rule()
            yield Static(
                "[dim]↑↓ 选择  ·  Enter 保存  ·  Esc 取消[/]",
                classes="hint",
            )

    def action_cancel(self) -> None:
        """关闭设置界面，丢弃未保存的修改。"""
        self.dismiss(None)

    def key_enter(self) -> None:
        """按 Enter 保存并关闭。"""
        self._save_and_dismiss()

    def _save_and_dismiss(self) -> None:
        """保存配置并关闭界面。"""
        radio_set = self.query_one("#theme-mode", RadioSet)
        pressed_index = radio_set.pressed_index
        if pressed_index < 0:
            pressed_index = next(
                (
                    i
                    for i, (_, v) in enumerate(_THEME_OPTIONS)
                    if v == self._config.theme_mode
                ),
                0,
            )

        _, selected_value = _THEME_OPTIONS[pressed_index]
        self._config.theme_mode = selected_value
        try:
            GlobalConfigManager.save(self._config)
        except Exception as e:
            self.app.notify(f"配置保存失败: {e}", severity="error")
            return
        self.dismiss(self._config)

    async def action_quit_app(self) -> None:
        """ctrl+c 退出程序。"""
        self.app.exit()

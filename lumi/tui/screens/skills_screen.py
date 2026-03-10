"""技能列表界面

展示所有可用技能，支持搜索过滤。
基于 ListScreen 基类实现，Enter 查看技能完整内容。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Rule, Static

from lumi.agents.tools.config import SkillConfig
from lumi.tui.screens.list_screen import ListScreen
from lumi.utils.token_counter import str_token_counter


class _SkillItem(Static):
    """单个技能条目

    第一行：指示符 + 技能名称 + token 数
    第二行：描述（单行截断）
    """

    DEFAULT_CSS = """
    _SkillItem {
        width: 100%;
        height: 3;
        padding: 0 2;
        color: $foreground;
    }
    _SkillItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, skill: SkillConfig, index: int) -> None:
        self._skill = skill
        self._index = index

        desc_tokens = str_token_counter(skill.description)
        prompt_tokens = str_token_counter(skill.prompt)

        desc = skill.description.replace("\n", " ").strip()
        if len(desc) > 55:
            desc = desc[:52] + "..."

        text = Text()
        text.append(f"› /{skill.name}", style="bold cyan")
        text.append(
            f" · desc ~{desc_tokens} tokens / prompt ~{prompt_tokens} tokens",
            style="dim",
        )
        text.append(f"\n  {desc}", style="dim")
        super().__init__(text, markup=False)

    @property
    def skill(self) -> SkillConfig:
        return self._skill

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态"""
        self.set_class(selected, "selected")


class _SkillDetailScreen(ModalScreen[None]):
    """技能详情弹窗，展示完整 prompt 内容。"""

    DEFAULT_CSS = """
    _SkillDetailScreen {
        align: center middle;
    }
    _SkillDetailScreen > Vertical {
        width: 90;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }
    _SkillDetailScreen .detail-desc {
        color: $text-muted;
        width: 100%;
        padding: 0 0 1 0;
    }
    _SkillDetailScreen .detail-content {
        height: auto;
        max-height: 60vh;
        padding: 0 1;
    }
    _SkillDetailScreen .detail-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def __init__(self, skill: SkillConfig) -> None:
        super().__init__()
        self._skill = skill

    def compose(self) -> ComposeResult:
        desc_tokens = str_token_counter(self._skill.description)
        prompt_tokens = str_token_counter(self._skill.prompt)
        container = Vertical()
        container.border_title = f"/{self._skill.name} · desc ~{desc_tokens} tokens / prompt ~{prompt_tokens} tokens"
        with container:
            yield Static(self._skill.description, classes="detail-desc")
            yield Rule()
            with VerticalScroll(classes="detail-content"):
                yield Static(self._skill.prompt, markup=False)
            yield Rule()
            yield Static("Esc back", classes="detail-hint")

    def _on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()
            event.stop()


class SkillsScreen(ListScreen[SkillConfig]):
    """技能列表界面

    显示所有可用技能，Enter 查看完整内容，Esc 关闭。
    """

    @property
    def screen_title(self) -> str:
        return "Skills"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter 查看 · Esc close"

    @property
    def empty_text(self) -> str:
        return "暂无可用技能"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的技能"

    def match_filter(self, item: SkillConfig, query: str) -> bool:
        return query in item.name.lower() or query in item.description.lower()

    def make_item_widget(self, item: SkillConfig, index: int) -> Widget:
        return _SkillItem(item, index)

    def get_dismiss_value(self, item: SkillConfig) -> str:
        return item.name

    def _on_key(self, event: Key) -> None:
        """Enter 打开详情弹窗而非 dismiss。"""
        if event.key == "enter":
            if self._filtered and 0 <= self._selected_index < len(self._filtered):
                skill = self._filtered[self._selected_index]
                self.app.push_screen(_SkillDetailScreen(skill))
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

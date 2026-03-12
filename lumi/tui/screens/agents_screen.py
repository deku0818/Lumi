"""Agent 列表界面

展示所有可用 Agent，支持搜索过滤。
基于 ListScreen 基类实现，Enter 查看 Agent 详情。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Rule, Static

from lumi.agents.tools.config import AgentConfig
from lumi.tui.screens.list_screen import ListScreen
from lumi.utils.token_counter import str_token_counter


class _AgentItem(Static):
    """单个 Agent 条目

    第一行：指示符 + Agent 名称 + 模型 + 工具数
    第二行：描述（单行截断）
    """

    DEFAULT_CSS = """
    _AgentItem {
        width: 100%;
        height: 3;
        padding: 0 2;
        color: $foreground;
    }
    _AgentItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, agent: AgentConfig, index: int) -> None:
        self._agent = agent
        self._index = index

        desc_tokens = str_token_counter(agent.description)
        prompt_tokens = str_token_counter(agent.system_prompt)
        model_info = agent.model or "default"

        desc = agent.description.replace("\n", " ").strip()
        if len(desc) > 55:
            desc = desc[:52] + "..."

        text = Text()
        text.append(f"› {agent.name}", style="bold cyan")
        text.append(
            f" · desc ~{desc_tokens} tokens / prompt ~{prompt_tokens} tokens"
            f" · model: {model_info}",
            style="dim",
        )
        text.append(f"\n  {desc}", style="dim")
        super().__init__(text, markup=False)

    @property
    def agent(self) -> AgentConfig:
        return self._agent

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        self.set_class(selected, "selected")


class _AgentDetailScreen(ModalScreen[None]):
    """Agent 详情弹窗，展示完整系统提示词和工具列表。"""

    DEFAULT_CSS = """
    _AgentDetailScreen {
        align: center middle;
    }
    _AgentDetailScreen > Vertical {
        width: 90;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }
    _AgentDetailScreen .detail-desc {
        color: $text-muted;
        width: 100%;
        padding: 0 0 1 0;
    }
    _AgentDetailScreen .detail-tools {
        color: $foreground;
        width: 100%;
        padding: 0 0 1 0;
    }
    _AgentDetailScreen .detail-content {
        height: auto;
        max-height: 60vh;
        padding: 0 1;
    }
    _AgentDetailScreen .detail-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }
    """

    def __init__(self, agent: AgentConfig) -> None:
        super().__init__()
        self._agent = agent

    def compose(self) -> ComposeResult:
        a = self._agent
        desc_tokens = str_token_counter(a.description)
        prompt_tokens = str_token_counter(a.system_prompt)
        model_info = a.model or "default"
        tools_str = ", ".join(a.tools) if a.tools else "无"

        container = Vertical()
        container.border_title = (
            f"{a.name} · desc ~{desc_tokens} tokens"
            f" / prompt ~{prompt_tokens} tokens · model: {model_info}"
        )
        with container:
            yield Static(a.description, classes="detail-desc")
            yield Static(f"Tools: {tools_str}", classes="detail-tools")
            yield Rule()
            with VerticalScroll(classes="detail-content"):
                yield Static(a.system_prompt, markup=False)
            yield Rule()
            yield Static("Esc back", classes="detail-hint")

    def _on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()
            event.stop()


class AgentsScreen(ListScreen[AgentConfig]):
    """Agent 列表界面

    显示所有可用 Agent，Enter 查看详情，Esc 关闭。
    """

    @property
    def screen_title(self) -> str:
        return "Agents"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter 查看 · Esc close"

    @property
    def empty_text(self) -> str:
        return "暂无可用 Agent"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的 Agent"

    def match_filter(self, item: AgentConfig, query: str) -> bool:
        return query in item.name.lower() or query in item.description.lower()

    def make_item_widget(self, item: AgentConfig, index: int) -> Widget:
        return _AgentItem(item, index)

    def get_dismiss_value(self, item: AgentConfig) -> str:
        return item.name

    def _on_key(self, event: Key) -> None:
        """Enter 打开详情弹窗而非 dismiss。"""
        if event.key == "enter":
            if self._filtered and 0 <= self._selected_index < len(self._filtered):
                agent = self._filtered[self._selected_index]
                self.app.push_screen(_AgentDetailScreen(agent))
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

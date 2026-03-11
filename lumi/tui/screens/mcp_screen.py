"""MCP 服务器状态查看界面

展示所有 MCP 服务器的连接状态、配置信息和工具列表。
支持搜索过滤，Enter 查看工具详情。基于 ListScreen 基类实现。
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, Rule, Static

from lumi.agents.tools.providers.mcp import MCPServerInfo, MCPToolInfo
from lumi.tui.screens.list_screen import ListScreen


_STATUS_ICONS: dict[str, str] = {
    "connected": "✔",
    "failed": "✘",
    "not_started": "○",
}


class _MCPServerItem(Static):
    """单个 MCP 服务器条目"""

    DEFAULT_CSS = """
    _MCPServerItem {
        width: 100%;
        height: auto;
        padding: 0 2;
        color: $foreground;
    }
    _MCPServerItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, info: MCPServerInfo, index: int) -> None:
        self._info = info
        self._index = index

        icon = _STATUS_ICONS.get(info.status, "?")
        args_str = " ".join(info.args) if info.args else ""
        tool_count = len(info.tools)

        # 状态图标颜色
        icon_style = {
            "connected": "bold green",
            "failed": "bold red",
            "not_started": "bold yellow",
        }.get(info.status, "dim")

        text = Text()
        text.append("› ", style="bold")
        text.append(f"{icon} ", style=icon_style)
        text.append(f"{info.name}\n", style="bold")
        text.append(f"  Command: {info.command}", style="dim")
        if args_str:
            text.append(f"\n  Args: {args_str}", style="dim")
        text.append(f"\n  Transport: {info.transport or 'N/A'}", style="dim")
        text.append(f"\n  Tools: {tool_count} tools", style="dim")

        super().__init__(text, markup=False)

    @property
    def info(self) -> MCPServerInfo:
        return self._info

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态"""
        self.set_class(selected, "selected")


class MCPScreen(ListScreen[MCPServerInfo]):
    """MCP 服务器状态界面

    显示所有 MCP 服务器列表，选中后可查看工具详情。
    """

    @property
    def screen_title(self) -> str:
        return "MCP Servers"

    @property
    def hint_text(self) -> str:
        return "↑↓ select · Enter 查看工具列表 · Esc close"

    @property
    def empty_text(self) -> str:
        return "未配置任何 MCP 服务器"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的服务器"

    def match_filter(self, item: MCPServerInfo, query: str) -> bool:
        return (
            query in item.name.lower()
            or query in item.command.lower()
            or query in item.status.lower()
        )

    def make_item_widget(self, item: MCPServerInfo, index: int) -> Widget:
        return _MCPServerItem(item, index)

    def get_dismiss_value(self, item: MCPServerInfo) -> str:
        return item.name

    def _on_key(self, event: Key) -> None:
        """Enter 时打开工具详情子界面而非 dismiss。"""
        if event.key == "enter":
            if self._filtered and 0 <= self._selected_index < len(self._filtered):
                server = self._filtered[self._selected_index]
                if server.tools:
                    self.app.push_screen(MCPToolsScreen(server))
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)


class _MCPToolItem(Static):
    """单个工具条目"""

    DEFAULT_CSS = """
    _MCPToolItem {
        width: 100%;
        height: auto;
        padding: 0 2;
        color: $foreground;
        border-bottom: solid $surface-darken-2;
    }
    _MCPToolItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, tool_info: MCPToolInfo, index: int) -> None:
        self._tool_info = tool_info
        self._index = index

        desc = tool_info.description.replace("\n", " ").strip()
        if len(desc) > 80:
            desc = desc[:77] + "..."

        text = Text()
        text.append(f"  {tool_info.name}", style="bold")
        if desc:
            text.append(f"\n  {desc}", style="dim italic")
        super().__init__(text, markup=False)

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        """设置选中状态"""
        self.set_class(selected, "selected")


class MCPToolsScreen(ModalScreen[None]):
    """MCP 工具详情界面

    显示某个服务器下的所有工具名称，支持搜索过滤。
    """

    BINDINGS = [
        ("escape", "cancel", "返回"),
    ]

    DEFAULT_CSS = """
    MCPToolsScreen {
        align: center middle;
    }

    MCPToolsScreen > Vertical {
        width: 70;
        height: 70%;
        max-height: 70%;
        background: $surface;
        border: round $accent;
        border-title-style: bold;
        border-title-color: $accent;
        padding: 1 2;
    }

    MCPToolsScreen .mts-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        width: 100%;
    }

    MCPToolsScreen .mts-search {
        margin-bottom: 1;
    }

    MCPToolsScreen .mts-list {
        height: 1fr;
    }

    MCPToolsScreen .mts-hint {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }

    MCPToolsScreen .mts-empty {
        text-align: center;
        color: $text-muted;
        width: 100%;
        padding: 2 0;
    }
    """

    def __init__(self, server: MCPServerInfo) -> None:
        super().__init__()
        self._server = server
        self._all_tools: list[MCPToolInfo] = list(server.tools)
        self._filtered: list[MCPToolInfo] = list(server.tools)
        self._selected_index: int = 0
        self._item_widgets: list[Widget] = []

    def compose(self) -> ComposeResult:
        title = f"{self._server.name} - Tools ({len(self._all_tools)})"
        container = Vertical()
        container.border_title = self._server.name
        with container:
            yield Label(title, classes="mts-title", id="mts-title")
            yield Input(placeholder="Search...", classes="mts-search", id="mts-search")
            yield Rule()
            yield VerticalScroll(id="mts-list", classes="mts-list")
            yield Rule()
            yield Static("↑↓ select · Esc 返回", classes="mts-hint")

    async def on_mount(self) -> None:
        """挂载后渲染列表并聚焦搜索框。"""
        await self._render_list()
        self.query_one("#mts-search", Input).focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        """搜索过滤。"""
        query = event.value.strip().lower()
        if query:
            self._filtered = [
                t
                for t in self._all_tools
                if query in t.name.lower() or query in t.description.lower()
            ]
        else:
            self._filtered = list(self._all_tools)
        self._selected_index = 0
        await self._render_list()
        title_label = self.query_one("#mts-title", Label)
        title_label.update(
            f"{self._server.name} - Tools "
            f"({len(self._filtered)} of {len(self._all_tools)})"
        )

    async def _render_list(self) -> None:
        """渲染工具列表。"""
        container = self.query_one("#mts-list", VerticalScroll)
        await container.remove_children()
        self._item_widgets.clear()

        if not self._filtered:
            hint = "没有匹配的工具" if self._all_tools else "该服务器没有工具"
            await container.mount(Static(hint, classes="mts-empty"))
            return

        for i, tool_info in enumerate(self._filtered):
            widget = _MCPToolItem(tool_info, i)
            self._item_widgets.append(widget)
            await container.mount(widget)

        self._update_selection()

    def _update_selection(self) -> None:
        """更新选中高亮。"""
        for widget in self._item_widgets:
            idx = getattr(widget, "index", -1)
            widget.set_selected(idx == self._selected_index)  # type: ignore[attr-defined]
        if self._item_widgets and 0 <= self._selected_index < len(self._item_widgets):
            self._item_widgets[self._selected_index].scroll_visible()

    def _on_key(self, event: Key) -> None:
        """键盘导航。"""
        match event.key:
            case "escape":
                self.dismiss(None)
                event.prevent_default()
                event.stop()
            case "up":
                if self._filtered:
                    self._selected_index = max(0, self._selected_index - 1)
                    self._update_selection()
                event.prevent_default()
                event.stop()
            case "down":
                if self._filtered:
                    self._selected_index = min(
                        len(self._filtered) - 1, self._selected_index + 1
                    )
                    self._update_selection()
                event.prevent_default()
                event.stop()

    def action_cancel(self) -> None:
        """Esc 返回。"""
        self.dismiss(None)

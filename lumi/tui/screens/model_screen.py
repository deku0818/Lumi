"""模型切换弹窗 — 把「供应商 × 模型」拍平成一个列表，Enter 切换为当前模型。

仅切换：新增 / 编辑 / 删除在桌面端配置页完成（共享 ~/.lumi/providers.json）。
基于 ListScreen 基类，自带搜索 / ↑↓ 导航 / Enter 确认 / Esc 取消。
Enter 时 dismiss 返回 "<provider_id>\\t<model>"，由 app 调 bridge.set_provider 应用。
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from lumi.tui.screens.list_screen import ListScreen

# dismiss 值分隔符：provider id 为 hex、model 名不含制表符，故安全
SEP = "\t"


class _ModelItem(Static):
    """单个「供应商 · 模型」条目：第一行 指示符 + 模型，第二行 供应商名。"""

    DEFAULT_CSS = """
    _ModelItem {
        width: 100%;
        height: 3;
        padding: 0 2;
        color: $foreground;
    }
    _ModelItem.selected {
        background: $accent 30%;
    }
    """

    def __init__(self, entry: dict, index: int, active: dict) -> None:
        self._entry = entry
        self._index = index
        is_active = entry["provider"] == active.get("provider") and entry[
            "model"
        ] == active.get("model")

        text = Text()
        text.append(
            "● " if is_active else "○ ", style="bold cyan" if is_active else "dim"
        )
        text.append(entry["model"], style="bold")
        if is_active:
            text.append("  (使用中)", style="dim")
        text.append(f"\n  {entry['name']}", style="dim")
        super().__init__(text, markup=False)

    @property
    def index(self) -> int:
        return self._index

    def set_selected(self, selected: bool) -> None:
        self.set_class(selected, "selected")


class ModelScreen(ListScreen[dict]):
    """模型切换界面。Enter 切换，Esc 关闭。

    entries 为拍平后的列表项：{"provider": id, "name": 供应商名, "model": 模型}。
    """

    def __init__(self, entries: list[dict], active: dict) -> None:
        self._active = active
        idx = next(
            (
                i
                for i, e in enumerate(entries)
                if e["provider"] == active.get("provider")
                and e["model"] == active.get("model")
            ),
            0,
        )
        super().__init__(entries, initial_index=idx)

    @property
    def screen_title(self) -> str:
        return "切换模型"

    @property
    def hint_text(self) -> str:
        return "↑↓ 选择 · Enter 切换 · Esc 关闭"

    @property
    def empty_text(self) -> str:
        return "暂无模型"

    @property
    def no_match_text(self) -> str:
        return "没有匹配的模型"

    def match_filter(self, item: dict, query: str) -> bool:
        return query in item["name"].lower() or query in item["model"].lower()

    def make_item_widget(self, item: dict, index: int) -> Widget:
        return _ModelItem(item, index, self._active)

    def get_dismiss_value(self, item: dict) -> str:
        return f"{item['provider']}{SEP}{item['model']}"

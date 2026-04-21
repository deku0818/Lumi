"""补全菜单组件 — 在 InputBar 下方展示匹配的斜杠命令列表"""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Static
from rich.cells import cell_len
from rich.text import Text

from lumi.tui.slash_commands.models import SlashCommand
from lumi.tui.theme import get_color

# 命令名列宽（含 / 前缀），描述占剩余空间并截断
_NAME_COL_WIDTH = 28

_VIEWPORT_SIZE = 12


def _truncate_by_width(text: str, max_width: int) -> str:
    """按显示宽度截断文本（CJK 字符占 2 列），超出时加省略号。

    Args:
        text: 原始文本
        max_width: 最大显示宽度

    Returns:
        截断后的文本
    """
    if cell_len(text) <= max_width:
        return text
    result = ""
    width = 0
    for ch in text:
        cw = cell_len(ch)
        if width + cw >= max_width:
            break
        result += ch
        width += cw
    return result + "…"


class CompletionMenu(Static):
    """补全菜单 — 展示匹配的斜杠命令列表。

    通过 ``show_commands()`` 更新候选列表，键盘上下键移动高亮，
    确认后发送 ``CommandSelected`` 消息通知父组件。
    左列命令名固定宽度，右列描述自动截断不换行。
    """

    DEFAULT_CSS = f"""
    CompletionMenu {{
        display: none;
        width: 100%;
        max-height: {_VIEWPORT_SIZE};
        padding: 0 1;
        background: transparent;
        color: $text;
        overflow-x: hidden;
    }}
    """

    class CommandSelected(Message):
        """用户选择了一个命令。"""

        def __init__(self, command_name: str) -> None:
            super().__init__()
            self.command_name = command_name

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands: tuple[SlashCommand, ...] = ()
        self._selected_index: int = 0
        self._viewport_start: int = 0

    def show_commands(self, commands: tuple[SlashCommand, ...]) -> None:
        """更新并展示命令列表。列表为空时自动隐藏。"""
        if not commands:
            self.hide()
            return
        if commands == self._commands:
            return
        self._commands = commands
        self._selected_index = 0
        self._viewport_start = 0
        self._render_menu()
        self.styles.display = "block"

    def hide(self) -> None:
        """隐藏菜单并清空命令列表。"""
        self.styles.display = "none"
        self._commands = ()
        self._selected_index = 0
        self._viewport_start = 0

    def move_selection(self, direction: int) -> None:
        """上下移动高亮选项。

        Args:
            direction: -1 上移，1 下移。
        """
        if not self._commands:
            return
        new_index = self._selected_index + direction
        self._selected_index = max(0, min(new_index, len(self._commands) - 1))
        self._render_menu()

    def confirm_selection(self) -> None:
        """确认当前高亮项，发送 ``CommandSelected`` 消息。"""
        if not self._commands:
            return
        command = self._commands[self._selected_index]
        self.post_message(self.CommandSelected(command.name))

    @property
    def is_visible(self) -> bool:
        """菜单是否可见。"""
        return self.styles.display != "none"

    def _adjust_viewport(self) -> None:
        """确保选中项在可见窗口内，必要时滑动窗口。"""
        if len(self._commands) <= _VIEWPORT_SIZE:
            self._viewport_start = 0
            return
        if self._selected_index < self._viewport_start:
            self._viewport_start = self._selected_index
        elif self._selected_index >= self._viewport_start + _VIEWPORT_SIZE:
            self._viewport_start = self._selected_index - _VIEWPORT_SIZE + 1

    def _render_menu(self) -> None:
        """根据当前命令列表和选中索引渲染菜单内容。

        左列固定宽度展示 /命令名，右列展示描述，每条命令严格一行。
        描述中的换行符替换为空格，超出宽度截断并加省略号。
        """
        # 计算可用总宽度，留出 padding；未挂载时回退默认值
        available_width = self.size.width if self.size.width > 0 else 80
        total_width = available_width - 2
        desc_width = max(total_width - _NAME_COL_WIDTH, 10)

        accent = get_color("accent")
        muted = get_color("text_muted")

        self._adjust_viewport()
        start = self._viewport_start
        end = min(start + _VIEWPORT_SIZE, len(self._commands))

        output = Text()
        for i in range(start, end):
            cmd = self._commands[i]
            if i > start:
                output.append("\n")
            # 命令名固定宽度，左对齐
            name_padded = f"/{cmd.name}".ljust(_NAME_COL_WIDTH)
            # 描述：去除换行，按显示宽度截断（CJK 字符占 2 列）
            desc = _truncate_by_width(
                " ".join(cmd.description.replace("\n", " ").split()),
                desc_width,
            )
            if i == self._selected_index:
                output.append(name_padded, style=f"bold {accent}")
                output.append(desc, style=accent)
            else:
                output.append(name_padded, style=f"bold {muted}")
                output.append(desc, style=muted)

        self.update(output)

"""CompletionMenu 属性测试"""

from __future__ import annotations

from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.widgets.completion_menu import CompletionMenu


async def _dummy_handler(extra_text: str = "") -> None:
    """测试用的空处理器"""


def _make_commands(n: int) -> tuple[SlashCommand, ...]:
    """生成 n 个测试命令"""
    return tuple(
        SlashCommand(
            name=f"cmd{i}",
            description=f"desc{i}",
            command_type=CommandType.BUILTIN,
            handler=_dummy_handler,
        )
        for i in range(n)
    )


# Feature: slash-commands, Property 9: 补全菜单导航边界
# **Validates: Requirements 4.5**
@settings(max_examples=100)
@given(
    num_commands=st.integers(min_value=1, max_value=10),
    directions=st.lists(
        st.sampled_from([-1, 1]),
        min_size=1,
        max_size=50,
    ),
)
async def test_navigation_bounds(num_commands: int, directions: list[int]) -> None:
    """任意方向键操作序列后，选中索引始终在 [0, len-1] 范围内"""
    menu = CompletionMenu()
    commands = _make_commands(num_commands)

    # 直接设置内部状态，绕过 show_commands 中的 self.update() 调用
    menu._commands = commands
    menu._selected_index = 0

    with patch.object(menu, "_render_menu"):
        for direction in directions:
            menu.move_selection(direction)
            assert 0 <= menu._selected_index <= len(commands) - 1, (
                f"索引 {menu._selected_index} 超出范围 [0, {len(commands) - 1}]，"
                f"方向: {direction}"
            )


# --- 单元测试 ---
# 需求: 4.3, 4.6, 4.7


async def test_confirm_selection_posts_message() -> None:
    """确认选择后发送 CommandSelected 消息，携带正确的 command_name。"""
    menu = CompletionMenu()
    commands = _make_commands(3)
    menu._commands = commands
    menu._selected_index = 1

    with (
        patch.object(menu, "_render_menu"),
        patch.object(menu, "post_message") as mock_post,
    ):
        menu.confirm_selection()
        mock_post.assert_called_once()
        msg = mock_post.call_args[0][0]
        assert isinstance(msg, CompletionMenu.CommandSelected)
        assert msg.command_name == "cmd1"


async def test_confirm_selection_empty_noop() -> None:
    """命令列表为空时，confirm_selection 不发送任何消息。"""
    menu = CompletionMenu()
    menu._commands = ()

    with patch.object(menu, "post_message") as mock_post:
        menu.confirm_selection()
        mock_post.assert_not_called()


async def test_show_commands_empty_hides() -> None:
    """传入空命令列表时，菜单隐藏且内部状态清空。"""
    menu = CompletionMenu()
    # 先设置一些状态
    menu._commands = _make_commands(2)
    menu._selected_index = 1

    with patch.object(menu, "_render_menu"):
        menu.show_commands(())

    assert menu._commands == ()
    assert menu._selected_index == 0


async def test_hide_clears_state() -> None:
    """hide() 清空命令列表、重置索引。"""
    menu = CompletionMenu()
    menu._commands = _make_commands(3)
    menu._selected_index = 2

    menu.hide()

    assert menu._commands == ()
    assert menu._selected_index == 0

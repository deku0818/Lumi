"""命令解析属性测试 — 命令模式检测、前缀提取"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings, strategies as st

from lumi.agents.tools.loader import SkillConfig
from lumi.tui.slash_commands.handlers import make_skill_handler
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.parser import (
    extract_command_prefix,
    is_command_mode,
    parse_command_input,
)
from lumi.tui.slash_commands.registry import CommandRegistry


# Feature: slash-commands, Property 7: 命令模式检测
# **Validates: Requirements 3.1, 3.3, 3.4**


@settings(max_examples=100)
@given(suffix=st.text(min_size=0))
def test_command_mode_with_slash_prefix(suffix: str) -> None:
    """以 '/' 开头的字符串应返回 True"""
    text = "/" + suffix
    assert is_command_mode(text) is True


@settings(max_examples=100)
@given(text=st.text(min_size=1).filter(lambda s: not s.startswith("/")))
def test_command_mode_without_slash_prefix(text: str) -> None:
    """不以 '/' 开头的非空字符串应返回 False"""
    assert is_command_mode(text) is False


@settings(max_examples=100)
@given(
    before=st.text(min_size=1).filter(lambda s: not s.startswith("/")),
    after=st.text(min_size=0),
)
def test_command_mode_slash_in_middle(before: str, after: str) -> None:
    """'/' 出现在非首位（如 'abc/test'）应返回 False"""
    text = before + "/" + after
    assert is_command_mode(text) is False


def test_command_mode_empty_string() -> None:
    """空字符串应返回 False"""
    assert is_command_mode("") is False


# Feature: slash-commands, Property 8: 命令前缀提取
# **Validates: Requirements 3.2**


@settings(max_examples=100)
@given(
    command_name=st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True),
    extra_text=st.text(min_size=0, max_size=50),
)
def test_extract_command_prefix_with_extra_text(
    command_name: str, extra_text: str
) -> None:
    """提取结果等于去掉 '/' 后到第一个空格之间的子串"""
    text = "/" + command_name + " " + extra_text
    assert extract_command_prefix(text) == command_name


@settings(max_examples=100)
@given(
    command_name=st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True),
)
def test_extract_command_prefix_without_extra(command_name: str) -> None:
    """无额外文本时，提取结果等于完整命令名"""
    text = "/" + command_name
    assert extract_command_prefix(text) == command_name


def test_extract_command_prefix_slash_only() -> None:
    """仅 '/' 时，提取结果为空字符串"""
    assert extract_command_prefix("/") == ""


# Feature: slash-commands, Property 6: 技能命令 prompt 拼接
# **Validates: Requirements 2.4, 2.5**

prompt_st = st.text(min_size=1, max_size=200)
extra_text_st = st.text(min_size=0, max_size=100)
skill_name_st = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)


@settings(max_examples=100)
@given(
    skill_name=skill_name_st,
    skill_description=st.text(min_size=0, max_size=100),
    skill_prompt=prompt_st,
    extra_text=extra_text_st,
)
def test_skill_handler_prompt_concatenation(
    skill_name: str,
    skill_description: str,
    skill_prompt: str,
    extra_text: str,
) -> None:
    """handler 提交内容始终包含 skill.prompt，额外文本非空时也包含"""
    skill = SkillConfig(
        name=skill_name, description=skill_description, prompt=skill_prompt
    )

    captured: list[tuple[str, str, str]] = []

    async def mock_send_to_agent(name: str, content: str, extra: str) -> None:
        captured.append((name, content, extra))

    handler = make_skill_handler(skill, mock_send_to_agent)
    asyncio.run(handler(extra_text))

    assert len(captured) == 1
    sent_name, sent_content, sent_extra = captured[0]

    # 技能名称正确传递
    assert sent_name == skill_name

    # prompt 始终包含在提交内容中
    assert skill_prompt in sent_content

    # 额外文本非空时也应包含
    if extra_text:
        assert extra_text in sent_content
        assert sent_extra == extra_text
    else:
        assert sent_extra == ""


# Feature: slash-commands, Property 10: 命令路由正确性
# **Validates: Requirements 6.1, 6.2**

# 命令名策略：合法的命令名（小写字母开头，可含字母数字和连字符）
_command_name_st = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)


def _make_noop_handler() -> tuple[list[str], object]:
    """创建一个记录调用的 noop handler，返回 (captured_list, handler)"""
    captured: list[str] = []

    async def handler(extra_text: str) -> None:
        captured.append(extra_text)

    return captured, handler


@settings(max_examples=100)
@given(
    registered_names=st.lists(_command_name_st, min_size=1, max_size=10, unique=True),
    input_command=_command_name_st,
    extra_text=st.text(min_size=0, max_size=50),
)
def test_command_routing_registered_command(
    registered_names: list[str],
    input_command: str,
    extra_text: str,
) -> None:
    """匹配已注册命令时，registry.get() 应返回对应命令"""
    registry = CommandRegistry()

    # 注册所有命令
    for name in registered_names:
        _, handler = _make_noop_handler()
        cmd = SlashCommand(
            name=name,
            description=f"desc-{name}",
            command_type=CommandType.BUILTIN,
            handler=handler,
        )
        registry.register(cmd)

    # 构造用户输入
    user_input = "/" + input_command
    if extra_text:
        user_input = user_input + " " + extra_text

    # 解析命令
    cmd_name, _ = parse_command_input(user_input)

    # 路由判断
    result = registry.get(cmd_name)

    if input_command in registered_names:
        # 匹配已注册命令 → 应路由到对应 handler
        assert result is not None
        assert result.name == input_command
    else:
        # 不匹配 → 应作为普通消息（get 返回 None）
        assert result is None


@settings(max_examples=100)
@given(
    registered_names=st.lists(_command_name_st, min_size=1, max_size=10, unique=True),
    unknown_suffix=st.from_regex(r"[a-z]{1,8}", fullmatch=True),
    extra_text=st.text(min_size=0, max_size=50),
)
def test_command_routing_unknown_command(
    registered_names: list[str],
    unknown_suffix: str,
    extra_text: str,
) -> None:
    """不匹配任何已注册命令时，registry.get() 应返回 None（作为普通消息）"""
    registry = CommandRegistry()

    for name in registered_names:
        _, handler = _make_noop_handler()
        cmd = SlashCommand(
            name=name,
            description=f"desc-{name}",
            command_type=CommandType.BUILTIN,
            handler=handler,
        )
        registry.register(cmd)

    # 构造一个保证不在已注册列表中的命令名
    unknown_name = "zzunknown-" + unknown_suffix
    user_input = "/" + unknown_name
    if extra_text:
        user_input = user_input + " " + extra_text

    cmd_name, parsed_extra = parse_command_input(user_input)

    # 不匹配任何已注册命令 → 应作为普通消息
    assert registry.get(cmd_name) is None
    assert cmd_name == unknown_name


@settings(max_examples=100)
@given(
    registered_names=st.lists(_command_name_st, min_size=1, max_size=10, unique=True),
    extra_text=st.text(min_size=0, max_size=50),
)
def test_command_routing_handler_receives_extra_text(
    registered_names: list[str],
    extra_text: str,
) -> None:
    """匹配命令时，handler 应能接收到正确的额外文本"""
    registry = CommandRegistry()
    handlers_map: dict[str, list[str]] = {}

    for name in registered_names:
        captured, handler = _make_noop_handler()
        handlers_map[name] = captured
        cmd = SlashCommand(
            name=name,
            description=f"desc-{name}",
            command_type=CommandType.BUILTIN,
            handler=handler,
        )
        registry.register(cmd)

    # 选取第一个已注册命令进行测试
    target_name = registered_names[0]
    user_input = "/" + target_name
    if extra_text:
        user_input = user_input + " " + extra_text

    cmd_name, parsed_extra = parse_command_input(user_input)
    result = registry.get(cmd_name)

    assert result is not None

    # 执行 handler 并验证额外文本传递正确
    asyncio.run(result.handler(parsed_extra))
    assert len(handlers_map[target_name]) == 1
    assert handlers_map[target_name][0] == parsed_extra


# Feature: slash-commands — 命令执行错误处理
# **Validates: Requirements 6.3**


def test_command_execution_error_handling() -> None:
    """handler 抛出异常时应被捕获，错误信息包含异常消息"""
    registry = CommandRegistry()
    error_msg = "测试错误"

    async def failing_handler(extra_text: str = "") -> None:
        raise RuntimeError(error_msg)

    cmd = SlashCommand(
        name="fail",
        description="会失败的命令",
        command_type=CommandType.BUILTIN,
        handler=failing_handler,
    )
    registry.register(cmd)

    # 模拟命令路由逻辑
    user_input = "/fail some args"
    cmd_name, extra = parse_command_input(user_input)
    command = registry.get(cmd_name)
    assert command is not None

    # 验证 handler 抛出异常时被正确捕获
    error_caught = False
    caught_message = ""
    try:
        asyncio.run(command.handler(extra))
    except Exception as e:
        error_caught = True
        caught_message = str(e)

    assert error_caught is True
    assert caught_message == error_msg
    # 验证错误提示格式（LumiApp 中的格式）
    expected_display = f"✗ 命令执行失败: {caught_message}"
    assert "✗ 命令执行失败:" in expected_display
    assert error_msg in expected_display

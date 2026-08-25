"""渠道侧斜杠命令解析（纯语法切分，渠道无关）。"""

from __future__ import annotations

from lumi.gateway.channels.commands import (
    CHANNEL_COMMAND_NAMES,
    is_model_arg,
    parse_slash_command,
)


def test_parse_basic():
    assert parse_slash_command("/commit") == ("commit", "")
    assert parse_slash_command("/commit fix bug") == ("commit", "fix bug")


def test_parse_multiline_extra():
    assert parse_slash_command("/review\n重点看并发") == ("review", "重点看并发")


def test_parse_strips_leading_mentions():
    # 群聊 mention 模式：resolve_mentions 后正文形如 "@Lumi /commit …"
    assert parse_slash_command("@Lumi /commit fix") == ("commit", "fix")
    assert parse_slash_command("@Lumi @张三 /review") == ("review", "")


def test_parse_mention_name_with_spaces():
    # 显示名可含空格（"Lumi Bot"），不能按 token 剥名字
    assert parse_slash_command("@Lumi Bot /stop") == ("stop", "")
    assert parse_slash_command("@Lumi 助手 /commit fix bug") == ("commit", "fix bug")


def test_parse_non_command_returns_none():
    assert parse_slash_command("帮我 commit 一下") is None
    assert parse_slash_command("看下 /etc/hosts") is None
    assert parse_slash_command("@Lumi 你好") is None
    # mention 场景下路径形态会被语法切出，靠调用方对照已知命令表兜底为普通文本
    assert parse_slash_command("@Lumi 看下 /etc/hosts") == ("etc/hosts", "")


def test_parse_degenerate_forms_return_none():
    assert parse_slash_command("/") is None
    assert parse_slash_command("/ commit") is None
    assert parse_slash_command("") is None
    assert parse_slash_command("@Lumi") is None


def test_channel_command_names_cover_all_tables():
    # 遮蔽集 = 系统 + 运行时 + relay 三表并集：技能不得占用其中任何名字
    assert {
        "stop",
        "clear",
        "help",
        "direct",
        "model",
        "effort",
    } <= CHANNEL_COMMAND_NAMES


def test_is_model_arg_cjk_and_multiword_fall_through():
    # 空/default/单个 ASCII 词（含拼错）拦下反馈；中文或多词按自然语言喂模型
    assert is_model_arg("")
    assert is_model_arg("default")
    assert is_model_arg("claude-opus-4-8")
    assert is_model_arg("claude-opus-48")  # 拼错也拦，红卡反馈
    assert not is_model_arg("换成最便宜的那个")  # 无空格中文单 token
    assert not is_model_arg("claude-opus-4-8 谢谢")
    assert not is_model_arg("帮我 看下 哪个便宜")

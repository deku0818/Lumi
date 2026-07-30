"""`lumi feishu` 命令行入口：配置读写往返、secret stdin、校验拦截、体检退出码。"""

from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import patch

from typer.testing import CliRunner

from lumi.cli import _CHECK_MARKS, app
from lumi.gateway.channels import store
from lumi.gateway.channels.feishu.checks import Check

runner = CliRunner()


@contextmanager
def _diagnosed(bot=(), minutes=()):
    """打桩三组体检，yield 妙记侧 mock 供调用断言。"""
    with (
        patch("lumi.gateway.channels.feishu.setup.local_env_checks", return_value=[]),
        patch("lumi.gateway.channels.feishu.setup.diagnose", return_value=list(bot)),
        patch(
            "lumi.gateway.channels.feishu.minutes.diagnose", return_value=list(minutes)
        ) as minutes_mock,
    ):
        yield minutes_mock


def test_config_set_then_show_roundtrip():
    """写入后显示的值可照抄回写入语法：bool 是 true/false、列表逗号连接。"""
    result = runner.invoke(
        app,
        ["feishu", "config", "app_id=cli_x", "allow_from=ou_1,ou_2", "workspace=/w"],
    )
    assert result.exit_code == 0, result.output
    assert store.load_feishu().allow_from == ["ou_1", "ou_2"]

    shown = runner.invoke(app, ["feishu", "config"])
    assert "app_id: cli_x" in shown.stdout
    assert "allow_from: ou_1,ou_2" in shown.stdout
    assert "enabled: false" in shown.stdout


def test_config_secret_from_stdin_and_masked_display():
    """app_secret=- 从 stdin 读，显示时打码——密钥不该出现在命令行参数或完整回显里。"""
    result = runner.invoke(
        app, ["feishu", "config", "app_secret=-"], input="topsecret-value\n"
    )
    assert result.exit_code == 0, result.output
    assert store.load_feishu().app_secret == "topsecret-value"

    shown = runner.invoke(app, ["feishu", "config"])
    assert "topsecret-value" not in shown.stdout
    assert "app_secret: topsec…" in shown.stdout


def test_config_short_secret_fully_masked():
    """短密文露 6 位前缀等于全露，改为只报「已设置」。"""
    runner.invoke(app, ["feishu", "config", "app_secret=-"], input="sekr3t\n")
    shown = runner.invoke(app, ["feishu", "config"])
    assert "sekr3t" not in shown.stdout
    assert "app_secret: （已设置）" in shown.stdout


def test_config_int_field_rejects_non_numeric():
    """非数字给用法级报错，不是 Python 栈回溯——这条 CLI 的调用方是零基础用户和 agent。"""
    result = runner.invoke(app, ["feishu", "config", "summary_max_concurrency=abc"])
    assert result.exit_code != 0
    assert "ValueError" not in (result.output + str(result.exception or ""))


def test_config_rejects_unknown_field():
    """拼错字段名当场退回并列出可用字段，不落盘。"""
    result = runner.invoke(app, ["feishu", "config", "app_ld=x"])
    assert result.exit_code != 0
    assert store.load_feishu().app_id == ""


def test_config_enable_without_workspace_fails():
    """启用必须绑定项目——与 desktop save_channel 同一条校验，两个入口不许有两套规则。"""
    result = runner.invoke(app, ["feishu", "config", "enabled=true"])
    assert result.exit_code == 1
    assert "绑定项目" in result.output
    assert store.load_feishu().enabled is False


def test_sync_skills_requires_workspace():
    """未绑项目时技能包无处可装，报下一步命令而不是空跑。"""
    result = runner.invoke(app, ["feishu", "sync-skills"])
    assert result.exit_code == 1
    assert "workspace=" in result.output


def test_sync_skills_reports_count():
    runner.invoke(app, ["feishu", "config", "workspace=/w"])
    with patch("lumi.gateway.toolbox.sync_lark_skills", return_value=3) as sync:
        result = runner.invoke(app, ["feishu", "sync-skills"])
    assert result.exit_code == 0
    sync.assert_called_once_with(None, "/w")
    assert "3 个技能" in result.stdout


def test_diagnose_prints_fix_and_exits_nonzero_on_error():
    """有 error 项时退出码非零，fix_url/fix_note 逐行带出供调用方引导用户。"""
    bad = asdict(
        Check(
            key="scopes",
            name="缺少机器人权限",
            tone="error",
            detail="未开通：im:message",
            fix_url="https://open.feishu.cn/app/x/auth",
            fix_note="开通后需发布版本",
        )
    )
    with _diagnosed(bot=[bad]):
        result = runner.invoke(app, ["feishu", "diagnose"])
    assert result.exit_code == 1
    assert "[×] 缺少机器人权限" in result.stdout
    assert "链接: https://open.feishu.cn/app/x/auth" in result.stdout


def test_diagnose_marks_render_on_legacy_console():
    """三个记号都取自 GBK 字符集：中文 Windows 的旧版控制台按 GBK 配字体，
    ✓ U+2713 这类字符会掉成方框（能不能写出去是 _force_utf8_stdio 的事）。

    只约束记号本身——体检文案里出现什么字符不该被这条测试管住。
    """
    for mark in _CHECK_MARKS.values():
        mark.encode("gbk")


def test_diagnose_includes_minutes_when_enabled():
    """妙记开着才追加妙记四项——没开时跑 lark-cli 子进程纯属浪费且必报错。"""
    ok = asdict(Check(key="auth", name="用户授权有效", group="妙记"))
    runner.invoke(app, ["feishu", "config", "workspace=/w", "minutes_enabled=true"])
    with _diagnosed(minutes=[ok]) as minutes:
        result = runner.invoke(app, ["feishu", "diagnose"])
    minutes.assert_called_once()
    assert "用户授权有效" in result.stdout

    runner.invoke(app, ["feishu", "config", "minutes_enabled=false"])
    with _diagnosed() as minutes:
        runner.invoke(app, ["feishu", "diagnose"])
    minutes.assert_not_called()


def test_diagnose_all_ok_exits_zero():
    """全绿退出码 0——agent 复检闭环靠它判定「配好了」。"""
    ok = asdict(Check(key="credentials", name="应用凭证有效"))
    with _diagnosed(bot=[ok]):
        result = runner.invoke(app, ["feishu", "diagnose"])
    assert result.exit_code == 0

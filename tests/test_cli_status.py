"""`lumi status` 的退出码契约——scripts/install.sh 靠它判部署成败。

只打印文字的"验证"拦不住任何东西：脚本必须能从退出码分辨「healthy / 没在跑 /
在跑但谁都能连」。改这几个数字就是改部署脚本的行为，故单独锁住。
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lumi.cli import app
from lumi.ops import ServiceStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("LUMI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LUMI_TOKEN", raising=False)


def run_with(state: str, monkeypatch):
    monkeypatch.setattr("lumi.ops.check_service", lambda *a, **k: ServiceStatus(state))
    return runner.invoke(app, ["status"])


def test_healthy_exits_zero(monkeypatch):
    assert run_with("guarded", monkeypatch).exit_code == 0


def test_down_exits_one(monkeypatch):
    assert run_with("down", monkeypatch).exit_code == 1


def test_error_exits_one(monkeypatch):
    assert run_with("error", monkeypatch).exit_code == 1


def test_unguarded_exits_three_not_two(monkeypatch):
    # 2 是 click 的用法错误码，而**旧版 lumi 没有 status 子命令**，敲上去正是退 2。
    # 占用 2 的话，一台装了旧版的机器会被部署脚本一口咬定「处于无鉴权状态」
    result = run_with("unguarded", monkeypatch)
    assert result.exit_code == 3
    assert "未设令牌" in result.output


def test_missing_subcommand_still_exits_two():
    # 上一条依赖的前提：click 确实把用法错误报成 2。它变了，3 这个选择就失去意义
    assert runner.invoke(app, ["nonexistent-command"]).exit_code == 2

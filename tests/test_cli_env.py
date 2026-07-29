"""`lumi env` 命令行入口：状态展示、参数校验、失败退出码，以及子进程环境导出。"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lumi.cli import _export_lumi_bin, app
from lumi.gateway.toolbox import ToolStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """home 指到 tmp：工具箱是机器级的（bin_dir 落 ~/.lumi），别碰开发机真实家目录。

    单例的重置由 conftest 的 reset_lumi_config 统一负责。
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("LUMI_CONFIG_DIR", raising=False)


def test_status_lists_each_source_and_bin_dir():
    """三种来源各自的呈现；缺失项没有版本与路径，行尾不留空白。"""
    state = {
        "tools": [
            {"name": "uv", "source": "system", "version": "0.11.32", "path": "/u/uv"},
            {"name": "rg", "source": "missing", "version": "", "path": ""},
            {"name": "node", "source": "toolbox", "version": "24.18.0", "path": "/b/n"},
        ],
        "bin_dir": "/box/bin",
    }
    with patch("lumi.gateway.toolbox.status_all", return_value=state):
        result = runner.invoke(app, ["env", "status"])
    assert result.exit_code == 0
    assert "uv: 系统 v0.11.32 /u/uv" in result.stdout
    assert "rg: 缺失\n" in result.stdout
    assert "node: 工具箱 v24.18.0 /b/n" in result.stdout
    assert "工具箱目录: /box/bin" in result.stdout


def test_install_rejects_unknown_tool():
    """拼错工具名当场退回，不进下载流程（否则会拿 None URL 去请求）。"""
    with patch("lumi.gateway.toolbox.install") as install:
        result = runner.invoke(app, ["env", "install", "nodejs"])
    assert result.exit_code != 0
    install.assert_not_called()


def test_install_skips_existing_tool():
    """已装的不重装：install() 是无条件覆盖，照装会在工具箱留一份轮不到的影子副本。"""
    have = ToolStatus("node", "system", "24.0.0", "/usr/bin/node")
    with (
        patch("lumi.gateway.toolbox.detect", return_value=have),
        patch("lumi.gateway.toolbox.install") as install,
    ):
        result = runner.invoke(app, ["env", "install", "node"])
    install.assert_not_called()
    assert "node: 系统 v24.0.0 /usr/bin/node" in result.stdout


def test_install_failure_exits_nonzero():
    """断网 / 代理不通是常态：报一行原因并以非零码退出，供调用方判定。"""
    missing = ToolStatus("rg", "missing")
    with (
        patch("lumi.gateway.toolbox.detect", return_value=missing),
        patch(
            "lumi.gateway.toolbox.install", side_effect=RuntimeError("connect timeout")
        ),
    ):
        result = runner.invoke(app, ["env", "install", "rg"])
    assert result.exit_code == 1


def test_install_reports_final_status():
    """装完打印该工具的最终状态，调用方无需再跑一次 status。"""
    done = ToolStatus("rg", "toolbox", "15.2.0", "/box/bin/rg")
    with (
        patch("lumi.gateway.toolbox.detect", return_value=ToolStatus("rg", "missing")),
        patch("lumi.gateway.toolbox.install", return_value=done),
    ):
        result = runner.invoke(app, ["env", "install", "rg"])
    assert result.exit_code == 0
    assert "rg: 工具箱 v15.2.0 /box/bin/rg" in result.stdout


def test_exports_lumi_bin_only(monkeypatch):
    """只导出 LUMI_BIN，且它必须是能跑的东西——agent 的 shell 要靠它回调本后端。

    配置目录不跟着导：硬导出会波及所有子进程，把嵌套的 `lumi -p` 也钉在 ~/.lumi，
    丢掉它本该发现的项目 `.lumi/`（prompts / skills / agents）。
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("LUMI_CONFIG_DIR", None)
    _export_lumi_bin()
    assert os.access(os.environ["LUMI_BIN"], os.X_OK)
    assert "LUMI_CONFIG_DIR" not in os.environ

"""`lumi mcp` 命令行：与 desktop MCP 面板同一份数据的增删查 + 连接测试。"""

from __future__ import annotations

import json
import stat

import pytest
from typer.testing import CliRunner

from lumi.cli import app

runner = CliRunner()


@pytest.fixture
def mcp_dirs(tmp_path, monkeypatch):
    """全局层钉进 tmp（LUMI_CONFIG_DIR）+ 一个项目目录，绝不碰真实 ~/.lumi。"""
    monkeypatch.setenv("LUMI_CONFIG_DIR", str(tmp_path / "global"))
    proj = tmp_path / "proj"
    proj.mkdir()
    return tmp_path / "global" / "mcp_server.json", proj


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _proj_file(proj):
    return proj / ".lumi" / "mcp_server.json"


def test_add_stdio_roundtrip(mcp_dirs):
    """默认项目层；name 之后的 -y 归 server 命令，不被当成本命令选项。"""
    _, proj = mcp_dirs
    r = runner.invoke(
        app, ["mcp", "add", "--project", str(proj), "chrome", "npx", "-y", "@x/mcp"]
    )
    assert r.exit_code == 0, r.output
    assert _read(_proj_file(proj))["chrome"] == {
        "command": "npx",
        "args": ["-y", "@x/mcp"],
        "transport": "stdio",
    }


def test_add_strips_claude_style_double_dash(mcp_dirs):
    """`claude mcp` 肌肉记忆的 `--` 分隔符不落进 args。"""
    _, proj = mcp_dirs
    r = runner.invoke(
        app, ["mcp", "add", "--project", str(proj), "s", "npx", "--", "-y", "pkg"]
    )
    assert r.exit_code == 0, r.output
    assert _read(_proj_file(proj))["s"]["args"] == ["-y", "pkg"]


def test_add_env_to_global_scope_is_0600(mcp_dirs):
    global_file, proj = mcp_dirs
    r = runner.invoke(
        app,
        [
            "mcp", "add", "--scope", "global", "--project", str(proj),
            "-e", "TOKEN=abc", "notion", "npx", "-y", "notion-mcp",
        ],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert _read(global_file)["notion"]["env"] == {"TOKEN": "abc"}
    # env 可含密钥，权限与 desktop RPC 写入一致
    assert stat.S_IMODE(global_file.stat().st_mode) == 0o600


def test_add_url_infers_http_and_parses_header(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(
        app,
        [
            "mcp", "add", "--project", str(proj),
            "-H", "Authorization: Bearer x", "linear", "https://l.app/mcp",
        ],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert _read(_proj_file(proj))["linear"] == {
        "url": "https://l.app/mcp",
        "transport": "streamable_http",
        "headers": {"Authorization": "Bearer x"},
    }


def test_add_url_sse_transport(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(
        app,
        ["mcp", "add", "--project", str(proj), "-t", "sse", "s", "https://x/sse"],
    )
    assert r.exit_code == 0, r.output
    assert _read(_proj_file(proj))["s"]["transport"] == "sse"


def test_add_bad_env_format_rejected(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(
        app, ["mcp", "add", "--project", str(proj), "-e", "NOEQ", "s", "cmd"]
    )
    assert r.exit_code != 0
    assert not _proj_file(proj).exists()


def test_add_json_and_get_project_overrides_global(mcp_dirs):
    """get 按合并语义取生效配置：项目层同名覆盖全局层。"""
    _, proj = mcp_dirs
    runner.invoke(
        app,
        ["mcp", "add-json", "--scope", "global", "--project", str(proj), "w",
         '{"command": "global-cmd"}'],
    )  # fmt: skip
    runner.invoke(
        app,
        ["mcp", "add-json", "--project", str(proj), "w", '{"command": "proj-cmd"}'],
    )
    r = runner.invoke(app, ["mcp", "get", "--project", str(proj), "w"])
    assert r.exit_code == 0, r.output
    assert "[project]" in r.stdout and "proj-cmd" in r.stdout


def test_add_json_rejects_non_object(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(app, ["mcp", "add-json", "--project", str(proj), "s", '["x"]'])
    assert r.exit_code != 0


def test_get_missing_exits_nonzero(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(app, ["mcp", "get", "--project", str(proj), "nope"])
    assert r.exit_code == 1


def test_list_shows_layers_and_disabled(mcp_dirs):
    global_file, proj = mcp_dirs
    runner.invoke(
        app,
        ["mcp", "add-json", "--scope", "global", "--project", str(proj), "g",
         '{"url": "https://g/mcp"}'],
    )  # fmt: skip
    runner.invoke(
        app,
        ["mcp", "add-json", "--project", str(proj), "p",
         '{"command": "npx", "disabled": true}'],
    )  # fmt: skip
    r = runner.invoke(app, ["mcp", "list", "--project", str(proj)])
    assert r.exit_code == 0, r.output
    assert "[global]" in r.stdout and "g: https://g/mcp" in r.stdout
    assert "[project]" in r.stdout and "p: npx（已禁用）" in r.stdout


def test_remove_auto_locates_layer(mcp_dirs):
    _, proj = mcp_dirs
    runner.invoke(app, ["mcp", "add", "--project", str(proj), "s", "cmd"])
    r = runner.invoke(app, ["mcp", "remove", "--project", str(proj), "s"])
    assert r.exit_code == 0, r.output
    assert _read(_proj_file(proj)) == {}


def test_remove_ambiguous_requires_scope(mcp_dirs):
    """两层同名不猜：要求显式 --scope，且两层文件都原样未动。"""
    global_file, proj = mcp_dirs
    for scope in ("global", "project"):
        runner.invoke(
            app,
            ["mcp", "add", "--scope", scope, "--project", str(proj), "dup", "cmd"],
        )
    r = runner.invoke(app, ["mcp", "remove", "--project", str(proj), "dup"])
    assert r.exit_code == 1
    assert "dup" in _read(global_file) and "dup" in _read(_proj_file(proj))

    r = runner.invoke(
        app, ["mcp", "remove", "--scope", "global", "--project", str(proj), "dup"]
    )
    assert r.exit_code == 0, r.output
    assert "dup" not in _read(global_file)
    assert "dup" in _read(_proj_file(proj))


def test_remove_missing_exits_nonzero(mcp_dirs):
    _, proj = mcp_dirs
    r = runner.invoke(app, ["mcp", "remove", "--project", str(proj), "nope"])
    assert r.exit_code == 1


def test_corrupt_file_blocks_add_not_wipes(mcp_dirs):
    """存量文件损坏时 add 报错退出，绝不用新 dict 覆盖抹掉已有配置。"""
    _, proj = mcp_dirs
    path = _proj_file(proj)
    path.parent.mkdir(parents=True)
    path.write_text("{ broken,,,", encoding="utf-8")
    r = runner.invoke(app, ["mcp", "add", "--project", str(proj), "s", "cmd"])
    assert r.exit_code == 1
    assert path.read_text(encoding="utf-8") == "{ broken,,,"


def test_test_command_reports_capabilities(mcp_dirs, monkeypatch):
    """test 走 test_mcp_server（真探测已在 test_mcp_rpc 覆盖），这里验输出与出口码。"""
    _, proj = mcp_dirs
    runner.invoke(app, ["mcp", "add", "--project", str(proj), "s", "cmd"])

    async def fake_probe(config, timeout=15.0):
        assert config["command"] == "cmd"
        return {
            "ok": True,
            "server": {"name": "srv", "version": "1.0"},
            "latency_ms": 5,
            "tools": [{"name": "add"}, {"name": "sub"}],
            "prompts": [],
            "resources": [],
        }

    monkeypatch.setattr("lumi.agents.tools.providers.mcp.test_mcp_server", fake_probe)
    r = runner.invoke(app, ["mcp", "test", "--project", str(proj), "s"])
    assert r.exit_code == 0, r.output
    assert "srv v1.0" in r.stdout and "add, sub" in r.stdout


def test_test_command_failure_exits_nonzero(mcp_dirs, monkeypatch):
    _, proj = mcp_dirs
    runner.invoke(app, ["mcp", "add", "--project", str(proj), "s", "cmd"])

    async def fake_probe(config, timeout=15.0):
        return {"ok": False, "error": "boom"}

    monkeypatch.setattr("lumi.agents.tools.providers.mcp.test_mcp_server", fake_probe)
    r = runner.invoke(app, ["mcp", "test", "--project", str(proj), "s"])
    assert r.exit_code == 1
    assert "boom" in r.output

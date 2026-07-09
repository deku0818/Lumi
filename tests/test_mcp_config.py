"""_load_merged_mcp_config：分层合并（全局 ∪ 项目）+ 剥离 disabled 元字段。"""

from __future__ import annotations

import json
from pathlib import Path

import lumi.agents.tools.providers.mcp as mcp


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_global_only_strips_disabled(tmp_path, monkeypatch):
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {
            "a": {"command": "npx", "args": ["a"], "transport": "stdio"},
            "b": {"command": "npx", "transport": "stdio", "disabled": True},
        },
    )
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: global_path)

    merged = mcp._load_merged_mcp_config(None)

    assert set(merged) == {"a"}  # disabled 条目被丢弃
    assert "disabled" not in merged["a"]  # 保留项的 disabled 键被 pop（本例本就没有）
    assert merged["a"] == {"command": "npx", "args": ["a"], "transport": "stdio"}


def test_project_overrides_global(tmp_path, monkeypatch):
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {
            "shared": {"command": "global-cmd", "transport": "stdio"},
            "only-global": {"command": "g", "transport": "stdio"},
        },
    )
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: global_path)

    project_dir = tmp_path / "proj"
    _write(
        project_dir / ".lumi" / "mcp_server.json",
        {
            "shared": {"command": "project-cmd", "transport": "stdio"},
            "only-project": {"command": "p", "transport": "stdio"},
        },
    )

    merged = mcp._load_merged_mcp_config(project_dir)

    assert set(merged) == {"shared", "only-global", "only-project"}
    assert merged["shared"]["command"] == "project-cmd"  # 项目同名覆盖全局


def test_disabled_key_popped_from_kept_entry(tmp_path, monkeypatch):
    """保留项若显式带 disabled=false，也须 pop 掉（绝不下传 adapter）。"""
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {"a": {"command": "npx", "transport": "stdio", "disabled": False}},
    )
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: global_path)

    merged = mcp._load_merged_mcp_config(None)

    assert merged == {"a": {"command": "npx", "transport": "stdio"}}


def test_project_equal_to_global_not_double_read(tmp_path, monkeypatch):
    """项目 .lumi 恰为全局 ~/.lumi 时不重复叠加（避免自我覆盖歧义）。"""
    home = tmp_path / "home"
    global_path = home / ".lumi" / "mcp_server.json"
    _write(global_path, {"a": {"command": "npx", "transport": "stdio"}})
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: global_path)

    # project_dir 的 .lumi/mcp_server.json 与全局同路径
    merged = mcp._load_merged_mcp_config(home)

    assert merged == {"a": {"command": "npx", "transport": "stdio"}}


def test_missing_files_return_empty(tmp_path, monkeypatch):
    nope = tmp_path / "nope" / "mcp_server.json"
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: nope)
    assert mcp._load_merged_mcp_config(tmp_path / "also-nope") == {}


def test_global_path_honors_env_override(tmp_path, monkeypatch):
    """显式 LUMI_CONFIG_DIR 覆盖被尊重（不再被硬编码 ~/.lumi 静默丢弃）。"""
    from lumi.utils.read_config import get_config

    # cli_config_dir 为空（desktop 默认），只设环境变量
    monkeypatch.setattr(get_config().discovery, "cli_config_dir", None, raising=False)
    monkeypatch.setenv("LUMI_CONFIG_DIR", str(tmp_path / "custom"))
    assert (
        mcp._global_mcp_config_path()
        == (tmp_path / "custom" / "mcp_server.json").resolve()
    )

    monkeypatch.delenv("LUMI_CONFIG_DIR", raising=False)
    assert mcp._global_mcp_config_path() == Path.home() / ".lumi" / "mcp_server.json"


async def test_invalidate_only_closes_changed_pools(tmp_path, monkeypatch):
    """借鉴 Claude Code 的 hash-diff：只作废 merged 配置真变了的池，没变的不碰。"""
    closed: list[str] = []

    class FakeManager:
        def __init__(self, config_hash: str) -> None:
            self._config_hash = config_hash

        async def close(self) -> None:
            closed.append(self._config_hash)

    # 两个池：X 的配置将变、Y 的不变
    monkeypatch.setattr(
        mcp, "_pools", {"/p/X": FakeManager("OLD-X"), "/p/Y": FakeManager("SAME-Y")}
    )

    def fake_merged(project_dir):
        key = str(project_dir)
        # X 变了（新 hash 与 OLD-X 不同），Y 保持 SAME-Y
        return {"changed": True} if key == "/p/X" else {"same": "y"}

    monkeypatch.setattr(mcp, "_load_merged_mcp_config", fake_merged)
    # 让 Y 的当前 hash 恰好等于其存量 hash "SAME-Y"
    monkeypatch.setattr(
        mcp,
        "_config_hash",
        lambda cfg: "SAME-Y" if cfg == {"same": "y"} else "NEW-X",
    )

    await mcp.invalidate_mcp_pools("global")

    assert closed == ["OLD-X"]  # 只关了变了的 X
    assert "/p/X" not in mcp._pools and "/p/Y" in mcp._pools  # Y 原样保留

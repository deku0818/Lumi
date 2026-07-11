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


def test_missing_transport_inferred(tmp_path, monkeypatch):
    """缺 transport 的配置（Claude Desktop 风格）加载侧补推：有 url → HTTP，否则 stdio。
    与连接测试同源（_normalize_server_config），杜绝「测试绿灯、加载报错」分歧。"""
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {
            "cmd": {"command": "npx", "args": ["-y", "some-server"]},
            "web": {"url": "https://example.com/mcp"},
        },
    )
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: global_path)

    merged = mcp._load_merged_mcp_config(None)

    assert merged["cmd"]["transport"] == "stdio"
    assert merged["web"]["transport"] == "streamable_http"


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


# ── 后台加载（不阻塞会话就绪）──


async def test_get_mcp_tools_nonblocking_returns_empty_and_spawns_load(
    tmp_path, monkeypatch
):
    """未加载的池：get_mcp_tools 立即返回空集并触发后台加载，绝不 await start。"""
    project = tmp_path / "proj"
    _write(
        project / ".lumi" / "mcp_server.json",
        {"slow": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: tmp_path / "none.json")
    monkeypatch.setattr(mcp, "_pools", {})
    monkeypatch.setattr(mcp, "_pool_load_tasks", {})

    started: list[str] = []

    async def fake_load(key, project_dir, use_interceptors):
        started.append(key)

    monkeypatch.setattr(mcp, "_load_pool", fake_load)

    tools = await mcp.get_mcp_tools(project_dir=project)

    assert tools == []  # 立即空集，不等池
    assert started == []  # 加载在后台 task 中，尚未被本协程 await
    task = mcp._pool_load_tasks[mcp._project_key(project)]
    await task  # 后台任务确实在跑
    assert started == [mcp._project_key(project)]


async def test_ensure_pool_loading_idempotent(tmp_path, monkeypatch):
    """在途加载未完成时重复 ensure 不再起新任务（按池单飞）。"""
    monkeypatch.setattr(mcp, "_pools", {})
    monkeypatch.setattr(mcp, "_pool_load_tasks", {})
    import asyncio

    release = asyncio.Event()
    calls: list[int] = []

    async def fake_load(key, project_dir, use_interceptors):
        calls.append(1)
        await release.wait()

    monkeypatch.setattr(mcp, "_load_pool", fake_load)

    mcp.ensure_pool_loading(None)
    await asyncio.sleep(0)  # 让任务启动
    mcp.ensure_pool_loading(None)  # 在途中：不应再起
    release.set()
    await mcp._pool_load_tasks[mcp._GLOBAL_POOL_KEY]
    assert calls == [1]


def test_pool_generation_bumps_on_invalidate(monkeypatch):
    """配置作废递增版本号：存活会话在轮首据此重建工具列表。"""
    monkeypatch.setattr(mcp, "_pool_generation", {})
    assert mcp.pool_generation(None) == 0
    mcp._pool_generation[mcp._GLOBAL_POOL_KEY] = 3
    assert mcp.pool_generation(None) == 3

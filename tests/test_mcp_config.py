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


class FakeManager:
    """已启动的假 manager：只承载 close/hash 语义，不连真 server。"""

    def __init__(self, config_hash: str, closed: list[str]) -> None:
        self._config_hash = config_hash
        self._closed = closed
        self.server_status: dict[str, dict] = {}

    @property
    def is_started(self) -> bool:
        return True

    async def close(self) -> None:
        self._closed.append(self._config_hash)


def _fake_pool(key: str, config_hash: str, closed: list[str]) -> mcp.McpPool:
    pool = mcp.McpPool(key)
    pool.manager = FakeManager(config_hash, closed)
    return pool


async def test_invalidate_only_closes_changed_pools(tmp_path, monkeypatch):
    """借鉴 Claude Code 的 hash-diff：只作废 merged 配置真变了的池，没变的不碰；
    被作废的池经 close 换代——generation 递增（存活会话轮首据此重建工具列表）、
    manager 换新一代空实例。"""
    closed: list[str] = []
    monkeypatch.setattr(
        mcp,
        "_pools",
        {
            "/p/X": _fake_pool("/p/X", "OLD-X", closed),
            "/p/Y": _fake_pool("/p/Y", "SAME-Y", closed),
        },
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
    x, y = mcp._pools["/p/X"], mcp._pools["/p/Y"]
    assert x.generation == 1  # close 换代必递增版本号
    assert not x.manager.is_started  # manager 已换新一代空实例
    assert y.generation == 0 and y.manager._config_hash == "SAME-Y"  # Y 原样保留


async def test_evict_bumps_generation(monkeypatch):
    """LRU 淘汰与作废共用 close 路径：被淘汰池的版本号同样递增，
    绑着它的存活会话轮首感知换代重建，不会卡着死工具。"""
    closed: list[str] = []
    monkeypatch.setattr(mcp, "_MAX_POOLS", 1)
    keep = _fake_pool("/p/keep", "K", closed)
    victim = _fake_pool("/p/victim", "V", closed)
    victim.last_used = 0.0
    keep.last_used = 1.0
    monkeypatch.setattr(mcp, "_pools", {"/p/keep": keep, "/p/victim": victim})

    await mcp._evict_lru_pools(keep=keep)

    assert closed == ["V"]
    assert victim.generation == 1
    assert keep.generation == 0


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

    started: list[str] = []

    async def fake_load(self):
        started.append(self.key)

    monkeypatch.setattr(mcp.McpPool, "_load", fake_load)

    tools = await mcp.get_mcp_tools(project_dir=project)

    assert tools == []  # 立即空集，不等池
    assert started == []  # 加载在后台 task 中，尚未被本协程 await
    pool = mcp._pools[mcp._project_key(project)]
    await pool._load_task  # 后台任务确实在跑
    assert started == [pool.key]


async def test_ensure_loading_idempotent(monkeypatch):
    """在途加载未完成时重复 ensure 不再起新任务（按池单飞）。"""
    monkeypatch.setattr(mcp, "_pools", {})
    import asyncio

    release = asyncio.Event()
    calls: list[int] = []

    async def fake_load(self):
        calls.append(1)
        await release.wait()

    monkeypatch.setattr(mcp.McpPool, "_load", fake_load)

    pool = mcp.pool_for(None)
    pool.ensure_loading()
    await asyncio.sleep(0)  # 让任务启动
    pool.ensure_loading()  # 在途中：不应再起
    release.set()
    await pool._load_task
    assert calls == [1]


async def test_cancelled_load_keeps_successor_registration(monkeypatch):
    """被 close 取消的旧加载任务不得清掉同池后继注册的新任务（_load 的 finally
    只清自己的登记）。走真 _load：manager.start 挂起时取消，旧任务退出后
    _load_task 仍指向后继登记。"""
    import asyncio

    monkeypatch.setattr(mcp, "_pools", {})
    monkeypatch.setattr(mcp, "_load_merged_mcp_config", lambda p: {"s": {"url": "x"}})

    started = asyncio.Event()

    class HangingManager:
        is_started = False
        server_status: dict[str, dict] = {}

        async def start(self, mcp_config):
            started.set()
            await asyncio.Event().wait()

    pool = mcp.pool_for(None)
    pool.manager = HangingManager()
    pool.ensure_loading()
    old_task = pool._load_task
    await started.wait()

    old_task.cancel()  # 模拟 close：取消旧任务，同 key 随即注册了新任务
    sentinel = asyncio.get_event_loop().create_future()
    pool._load_task = sentinel
    await asyncio.wait([old_task])

    assert pool._load_task is sentinel  # 旧任务的 finally 不得误删新登记
    sentinel.cancel()


async def test_first_server_added_bumps_cold_pool(tmp_path, monkeypatch):
    """从无到有添加首个 server：无配置时 get_mcp_tools 也登记池对象；invalidate
    对配置非空的冷池直接换代——否则存活会话轮首版本号比对恒 0==0，新 server
    到应用重启前都不生效。配置仍为空的冷池则不换代（无可重建）。"""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    monkeypatch.setattr(mcp, "_global_mcp_config_path", lambda: tmp_path / "none.json")
    monkeypatch.setattr(mcp, "_pools", {})

    assert await mcp.get_mcp_tools(project_dir=project) == []  # 无配置：空集
    assert mcp._project_key(project) in mcp._pools  # 但池对象已挂名
    assert mcp.pool_generation(project) == 0

    await mcp.invalidate_mcp_pools("project", project)
    assert mcp.pool_generation(project) == 0  # 配置仍空：不换代

    _write(
        project / ".lumi" / "mcp_server.json",
        {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )
    await mcp.invalidate_mcp_pools("project", project)
    assert mcp.pool_generation(project) == 1  # 冷池换代：会话轮首感知并重建


async def test_wait_ready_retries_new_generation_after_close(monkeypatch):
    """close 换 manager 必须在排水前生效：停驻在 wait_ready 的等待者先于 close 被
    唤醒（done-callback FIFO），若此刻 manager 未换新，等待者会把旧 manager 误判
    为现任、按终态静默返回零工具；换代先行则 identity 失效→对新一代重试。"""
    import asyncio

    monkeypatch.setattr(mcp, "_pools", {})
    monkeypatch.setattr(
        mcp,
        "_load_merged_mcp_config",
        lambda p: {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )

    loads: list[int] = []

    async def fake_load(self):
        loads.append(1)
        if len(loads) == 1:
            await asyncio.Event().wait()  # 第一代：挂死等 close 取消
        self.manager._started = True  # 第二代：立即完成

    monkeypatch.setattr(mcp.McpPool, "_load", fake_load)

    pool = mcp.pool_for(None)
    pool.ensure_loading()
    waiter = asyncio.create_task(pool.wait_ready())
    await asyncio.sleep(0)  # 等待者停驻在 asyncio.wait([task])

    await pool.close()
    await asyncio.wait_for(waiter, 1)

    assert len(loads) == 2  # 对新一代重试了加载，而非静默返回
    assert pool.manager.is_started


async def test_close_all_pools_latch_blocks_new_loads(monkeypatch):
    """关停闩：close_all_pools 之后 ensure_loading 一律不再受理（清理后残存的
    后台任务再触发也不会 spawn 新子进程），wait_ready 无任务可等立即返回不挂死。"""
    import asyncio

    monkeypatch.setattr(mcp, "_pools", {})
    monkeypatch.setattr(mcp, "_shutting_down", False)  # 测试间不泄漏闩状态
    monkeypatch.setattr(mcp, "_kill_child_processes", lambda: None)
    monkeypatch.setattr(
        mcp,
        "_load_merged_mcp_config",
        lambda p: {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )

    await mcp.close_all_pools()

    pool = mcp.pool_for(None)
    pool.ensure_loading()
    assert pool._load_task is None  # 闩已落：不起新加载
    await asyncio.wait_for(pool.wait_ready(), 1)  # 不挂死

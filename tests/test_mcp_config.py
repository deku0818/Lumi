"""load_merged_mcp_config：分层合并（全局 ∪ 项目）+ 剥离 disabled 元字段；会话池换代。"""

from __future__ import annotations

import json
from pathlib import Path

import lumi.agents.tools.providers.mcp as mcp
from lumi.agents.tools.providers.mcp import config, pool, procs


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
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    merged = config.load_merged_mcp_config(None)

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
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    merged = config.load_merged_mcp_config(None)

    assert merged["cmd"]["transport"] == "stdio"
    assert merged["web"]["transport"] == "streamable_http"


def test_missing_args_filled_for_stdio_only(tmp_path, monkeypatch):
    """命令自足（无参数可传）时配置里没有 ``args``，但 adapter 硬性要求 stdio 必须带
    （缺则抛「'args' parameter is required for stdio connection」）——加载侧补空列表。
    HTTP 条目不补：adapter 的 ``**params`` 全透传，混入未知键会 TypeError。"""
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {
            "cmd": {"command": "npx", "transport": "stdio"},
            "web": {"url": "https://example.com/mcp"},
        },
    )
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    merged = config.load_merged_mcp_config(None)

    assert merged["cmd"]["args"] == []
    assert "args" not in merged["web"]


def test_project_overrides_global(tmp_path, monkeypatch):
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {
            "shared": {"command": "global-cmd", "transport": "stdio"},
            "only-global": {"command": "g", "transport": "stdio"},
        },
    )
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    project_dir = tmp_path / "proj"
    _write(
        project_dir / ".lumi" / "mcp_server.json",
        {
            "shared": {"command": "project-cmd", "transport": "stdio"},
            "only-project": {"command": "p", "transport": "stdio"},
        },
    )

    merged = config.load_merged_mcp_config(project_dir)

    assert set(merged) == {"shared", "only-global", "only-project"}
    assert merged["shared"]["command"] == "project-cmd"  # 项目同名覆盖全局


def test_disabled_key_popped_from_kept_entry(tmp_path, monkeypatch):
    """保留项若显式带 disabled=false，也须 pop 掉（绝不下传 adapter）。

    ``args: []`` 是归一化补的——adapter 对缺 args 的 stdio 直接抛
    「'args' parameter is required for stdio connection」。
    """
    global_path = tmp_path / "home" / ".lumi" / "mcp_server.json"
    _write(
        global_path,
        {"a": {"command": "npx", "transport": "stdio", "disabled": False}},
    )
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    merged = config.load_merged_mcp_config(None)

    assert merged == {"a": {"command": "npx", "transport": "stdio", "args": []}}


def test_project_equal_to_global_not_double_read(tmp_path, monkeypatch):
    """项目 .lumi 恰为全局 ~/.lumi 时不重复叠加（避免自我覆盖歧义）。"""
    home = tmp_path / "home"
    global_path = home / ".lumi" / "mcp_server.json"
    _write(global_path, {"a": {"command": "npx", "transport": "stdio"}})
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: global_path)

    # project_dir 的 .lumi/mcp_server.json 与全局同路径
    merged = config.load_merged_mcp_config(home)

    assert merged == {"a": {"command": "npx", "transport": "stdio", "args": []}}


def test_missing_files_return_empty(tmp_path, monkeypatch):
    nope = tmp_path / "nope" / "mcp_server.json"
    monkeypatch.setattr(config, "global_mcp_config_path", lambda: nope)
    assert config.load_merged_mcp_config(tmp_path / "also-nope") == {}


def test_global_path_honors_env_override(tmp_path, monkeypatch):
    """显式 LUMI_CONFIG_DIR 覆盖被尊重（不再被硬编码 ~/.lumi 静默丢弃）。"""
    from lumi.utils.config import get_config

    # explicit_dir 为空（desktop 默认），只设环境变量
    monkeypatch.setattr(get_config().discovery, "explicit_dir", None)
    monkeypatch.setenv("LUMI_CONFIG_DIR", str(tmp_path / "custom"))
    assert (
        config.global_mcp_config_path()
        == (tmp_path / "custom" / "mcp_server.json").resolve()
    )

    monkeypatch.delenv("LUMI_CONFIG_DIR", raising=False)
    assert config.global_mcp_config_path() == Path.home() / ".lumi" / "mcp_server.json"


class FakeManager:
    """已启动的假 manager：只承载 close 语义，不连真 server。tag 标识被关的是哪一代。"""

    def __init__(self, tag: str, closed: list[str]) -> None:
        self.tag = tag
        self._closed = closed
        self.server_status: dict[str, dict] = {}

    @property
    def is_started(self) -> bool:
        return True

    async def close(self) -> None:
        self._closed.append(self.tag)


def _fake_pool(key: str, config_hash: str, closed: list[str]) -> pool.McpPool:
    p = pool.McpPool(key)
    p.manager = FakeManager(config_hash, closed)
    # 已启动池：attempted_hash 即 _load 时写入的当前配置 hash，sync_config 只比对它
    p.attempted_hash = config_hash
    return p


async def test_invalidate_only_closes_changed_pools(tmp_path, monkeypatch):
    """借鉴 Claude Code 的 hash-diff：只作废 merged 配置真变了的池，没变的不碰；
    被作废的池经 close 换代——generation 递增（存活会话轮首据此重建工具列表）、
    manager 换新一代空实例。"""
    closed: list[str] = []
    monkeypatch.setattr(
        pool,
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

    monkeypatch.setattr(pool, "load_merged_mcp_config", fake_merged)
    # 让 Y 的当前 hash 恰好等于其存量 hash "SAME-Y"
    monkeypatch.setattr(
        pool,
        "config_hash",
        lambda cfg: "SAME-Y" if cfg == {"same": "y"} else "NEW-X",
    )

    await mcp.invalidate_mcp_pools("global")

    assert closed == ["OLD-X"]  # 只关了变了的 X
    x, y = pool._pools["/p/X"], pool._pools["/p/Y"]
    assert x.generation == 1  # close 换代必递增版本号
    assert not x.manager.is_started  # manager 已换新一代空实例
    assert y.generation == 0 and y.manager.tag == "SAME-Y"  # Y 原样保留


async def test_evict_bumps_generation(monkeypatch):
    """LRU 淘汰与作废共用 close 路径：被淘汰池的版本号同样递增，
    绑着它的存活会话轮首感知换代重建，不会卡着死工具。"""
    closed: list[str] = []
    monkeypatch.setattr(pool, "_MAX_POOLS", 1)
    keep = _fake_pool("/p/keep", "K", closed)
    victim = _fake_pool("/p/victim", "V", closed)
    victim.last_used = 0.0
    keep.last_used = 1.0
    monkeypatch.setattr(pool, "_pools", {"/p/keep": keep, "/p/victim": victim})

    await pool._evict_lru_pools(keep=keep)

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
    monkeypatch.setattr(
        config, "global_mcp_config_path", lambda: tmp_path / "none.json"
    )
    monkeypatch.setattr(pool, "_pools", {})

    started: list[str] = []

    async def fake_load(self):
        started.append(self.key)

    monkeypatch.setattr(pool.McpPool, "_load", fake_load)

    tools = await mcp.get_mcp_tools(project_dir=project)

    assert tools == []  # 立即空集，不等池
    assert started == []  # 加载在后台 task 中，尚未被本协程 await
    p = pool._pools[pool._project_key(project)]
    await p._load_task  # 后台任务确实在跑
    assert started == [p.key]


async def test_ensure_loading_idempotent(monkeypatch):
    """在途加载未完成时重复 ensure 不再起新任务（按池单飞）。"""
    monkeypatch.setattr(pool, "_pools", {})
    import asyncio

    release = asyncio.Event()
    calls: list[int] = []

    async def fake_load(self):
        calls.append(1)
        await release.wait()

    monkeypatch.setattr(pool.McpPool, "_load", fake_load)

    p = mcp.pool_for(None)
    p.ensure_loading()
    await asyncio.sleep(0)  # 让任务启动
    p.ensure_loading()  # 在途中：不应再起
    release.set()
    await p._load_task
    assert calls == [1]


async def test_cancelled_load_keeps_successor_registration(monkeypatch):
    """被 close 取消的旧加载任务不得清掉同池后继注册的新任务（_load 的 finally
    只清自己的登记）。走真 _load：manager.start 挂起时取消，旧任务退出后
    _load_task 仍指向后继登记。"""
    import asyncio

    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda p: {"s": {"url": "x"}})

    started = asyncio.Event()

    class HangingManager:
        is_started = False
        server_status: dict[str, dict] = {}

        async def start(self, mcp_config):
            started.set()
            await asyncio.Event().wait()

    p = mcp.pool_for(None)
    p.manager = HangingManager()
    p.ensure_loading()
    old_task = p._load_task
    await started.wait()

    old_task.cancel()  # 模拟 close：取消旧任务，同 key 随即注册了新任务
    sentinel = asyncio.get_event_loop().create_future()
    p._load_task = sentinel
    await asyncio.wait([old_task])

    assert p._load_task is sentinel  # 旧任务的 finally 不得误删新登记
    sentinel.cancel()


async def test_first_server_added_bumps_cold_pool(tmp_path, monkeypatch):
    """从无到有添加首个 server：无配置时 get_mcp_tools 也登记池对象；invalidate
    对配置变了的冷池换代——否则存活会话轮首版本号比对恒 0==0，新 server
    到应用重启前都不生效。配置仍为空（hash 未变）的冷池则不换代（无可重建）。"""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        config, "global_mcp_config_path", lambda: tmp_path / "none.json"
    )
    monkeypatch.setattr(pool, "_pools", {})

    assert await mcp.get_mcp_tools(project_dir=project) == []  # 无配置：空集
    assert pool._project_key(project) in pool._pools  # 但池对象已挂名
    assert mcp.pool_generation(project) == 0

    await mcp.invalidate_mcp_pools("project", project)
    assert mcp.pool_generation(project) == 0  # 配置仍空：不换代

    _write(
        project / ".lumi" / "mcp_server.json",
        {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )
    await mcp.invalidate_mcp_pools("project", project)
    assert mcp.pool_generation(project) == 1  # 冷池换代：会话轮首感知并重建


# ── 轮首自失效（进程外写入：lumi mcp CLI / 手改文件，没有 RPC invalidate 通知）──


async def test_refresh_closes_started_pool_on_external_change(monkeypatch):
    closed: list[str] = []
    p = _fake_pool("/p/X", "OLD", closed)
    monkeypatch.setattr(pool, "_pools", {"/p/X": p})
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda p: {"new": 1})
    monkeypatch.setattr(pool, "config_hash", lambda cfg: "NEW")

    await mcp.refresh_pool_config(Path("/p/X"))

    assert closed == ["OLD"]
    assert p.generation == 1  # 换代：会话轮首据此重建工具列表


async def test_refresh_noop_when_unchanged(monkeypatch):
    closed: list[str] = []
    p = _fake_pool("/p/X", "SAME", closed)
    monkeypatch.setattr(pool, "_pools", {"/p/X": p})
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda p: {"x": 1})
    monkeypatch.setattr(pool, "config_hash", lambda cfg: "SAME")

    await mcp.refresh_pool_config(Path("/p/X"))

    assert closed == [] and p.generation == 0


async def test_refresh_skips_inflight_load(monkeypatch):
    """在途首次加载可横跨多条消息，轮首自查不得打断（打断则永不收敛）。"""
    import asyncio

    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(
        pool,
        "load_merged_mcp_config",
        lambda p: (_ for _ in ()).throw(AssertionError("loading 池不应读配置")),
    )
    p = mcp.pool_for(None)
    p._load_task = asyncio.get_event_loop().create_future()  # 在途

    await mcp.refresh_pool_config(None)

    assert p.generation == 0
    p._load_task.cancel()


async def test_refresh_cold_failed_pool_retries_only_on_config_change(monkeypatch):
    """加载失败的终态冷池：配置没变绝不轮轮重试（每条消息重 spawn 坏 server），
    配置真变了才换代重试——靠 _load 失败路径也记下的 attempted_hash 区分。"""
    monkeypatch.setattr(pool, "_pools", {})
    holder = {"cfg": {"s": {"transport": "streamable_http", "url": "http://x/mcp"}}}
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda p: holder["cfg"])

    class FailingManager:
        is_started = False
        server_status: dict[str, dict] = {}

        async def start(self, mcp_config):
            raise RuntimeError("boom")

    p = mcp.pool_for(None)
    p.manager = FailingManager()
    p.ensure_loading()
    await p._load_task  # 失败终态（异常被 _load 吞掉、不换代）
    assert p.generation == 0
    assert p.attempted_hash == pool.config_hash(holder["cfg"])

    await mcp.refresh_pool_config(None)  # 配置未变：不重试
    assert p.generation == 0

    holder["cfg"] = {"s": {"transport": "streamable_http", "url": "http://y/mcp"}}
    await mcp.refresh_pool_config(None)  # 配置变了：换代唤醒会话重建重载
    assert p.generation == 1


async def test_refresh_wakes_cold_pool_on_first_server(monkeypatch):
    """空配置从未加载的冷池：首个 server 出现（CLI 写入）即换代，空转不换代。"""
    monkeypatch.setattr(pool, "_pools", {})
    holder: dict = {"cfg": {}}
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda p: holder["cfg"])

    p = mcp.pool_for(None)
    await mcp.refresh_pool_config(None)
    assert p.generation == 0  # 仍是空配置：不动

    holder["cfg"] = {"s": {"transport": "streamable_http", "url": "http://x/mcp"}}
    await mcp.refresh_pool_config(None)
    assert p.generation == 1


async def test_wait_ready_retries_new_generation_after_close(monkeypatch):
    """close 换 manager 必须在排水前生效：停驻在 wait_ready 的等待者先于 close 被
    唤醒（done-callback FIFO），若此刻 manager 未换新，等待者会把旧 manager 误判
    为现任、按终态静默返回零工具；换代先行则 identity 失效→对新一代重试。"""
    import asyncio

    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(
        pool,
        "load_merged_mcp_config",
        lambda p: {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )

    loads: list[int] = []

    async def fake_load(self):
        loads.append(1)
        if len(loads) == 1:
            await asyncio.Event().wait()  # 第一代：挂死等 close 取消
        self.manager._started = True  # 第二代：立即完成

    monkeypatch.setattr(pool.McpPool, "_load", fake_load)

    p = mcp.pool_for(None)
    p.ensure_loading()
    waiter = asyncio.create_task(p.wait_ready())
    await asyncio.sleep(0)  # 等待者停驻在 asyncio.wait([task])

    await p.close()
    await asyncio.wait_for(waiter, 1)

    assert len(loads) == 2  # 对新一代重试了加载，而非静默返回
    assert p.manager.is_started


async def test_close_all_pools_latch_blocks_new_loads(monkeypatch):
    """关停闩：close_all_pools 之后 ensure_loading 一律不再受理（清理后残存的
    后台任务再触发也不会 spawn 新子进程），wait_ready 无任务可等立即返回不挂死。"""
    import asyncio

    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(pool, "_shutting_down", False)  # 测试间不泄漏闩状态
    monkeypatch.setattr(pool, "kill_child_processes", lambda: None)
    monkeypatch.setattr(
        pool,
        "load_merged_mcp_config",
        lambda p: {"s": {"transport": "streamable_http", "url": "http://x/mcp"}},
    )

    await mcp.close_all_pools()

    p = mcp.pool_for(None)
    p.ensure_loading()
    assert p._load_task is None  # 闩已落：不起新加载
    await asyncio.wait_for(p.wait_ready(), 1)  # 不挂死


def test_parent_to_children_parses_powershell_table():
    """Windows 进程表解析：只认 "<ppid> <pid>" 两列数字行，噪声行丢弃。

    wmic 已被 Windows 淘汰（24H2 起默认不装），改用 PowerShell 一次取全表；
    它的输出可能夹带空行 / 中文告警，解析不能被这些行带崩。
    """
    table = "1 100\r\n100 200\r\n\r\n无法加载配置文件\r\n100 201\r\nbad line here\r\n"
    assert procs._parent_to_children(table) == {1: [100], 100: [200, 201]}


def test_collect_descendant_pids_windows_walks_tree(monkeypatch):
    """整棵后代树都要收到（原实现逐层 spawn 一个 wmic，现在本地建树）。"""
    import subprocess

    table = "1 10\n10 20\n10 21\n20 30\n999 40\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, table, ""),
    )
    assert sorted(procs._collect_descendant_pids_windows(10)) == [20, 21, 30]


def test_collect_descendant_pids_windows_survives_cycle(monkeypatch):
    """PID 复用可能让 ppid 关系成环，不能转不出来。"""
    import subprocess

    table = "10 20\n20 10\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, table, ""),
    )
    assert procs._collect_descendant_pids_windows(10) == [20]

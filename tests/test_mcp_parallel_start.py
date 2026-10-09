import asyncio
from types import SimpleNamespace

from lumi.agents.tools.providers.mcp import pool


async def test_persistent_and_stateless_servers_start_together(monkeypatch):
    manager = pool.MCPSessionManager()
    entered = set()
    all_entered = asyncio.Event()

    async def load(name):
        entered.add(name)
        if len(entered) == 3:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), 1)
        if name == "broken":
            raise ValueError("unavailable")
        return [SimpleNamespace(name=name, args_schema={})]

    class Client:
        def __init__(self, config, **kwargs):
            self.name = next(iter(config))

        async def get_tools(self):
            return await load(self.name)

    async def persistent(client, name, interceptors):
        return await load(name)

    monkeypatch.setattr(pool, "MultiServerMCPClient", Client)
    monkeypatch.setattr(manager, "_load_persistent", persistent)
    monkeypatch.setattr(manager, "_watch_session_lost", lambda tool: None)
    tools = await manager.start(
        {
            "stdio": {"transport": "stdio"},
            "http": {"transport": "streamable_http"},
            "broken": {"transport": "sse"},
        }
    )
    assert [t.name for t in tools] == ["stdio", "http"]
    assert manager.server_status["stdio"]["ok"]
    assert manager.server_status["http"]["ok"]
    assert not manager.server_status["broken"]["ok"]
    await manager.close()


async def test_project_starts_do_not_share_a_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(pool, "_shutting_down", False)
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda project: {"server": {}})
    monkeypatch.setattr(pool, "_on_pool_loaded", None)
    entered = 0
    all_entered = asyncio.Event()

    async def start(self, config):
        nonlocal entered
        entered += 1
        if entered == 2:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), 1)
        self._started = True
        return []

    monkeypatch.setattr(pool.MCPSessionManager, "start", start)
    a, b = pool.pool_for(tmp_path / "a"), pool.pool_for(tmp_path / "b")
    await asyncio.gather(a.wait_ready(), a.wait_ready(), b.wait_ready())
    assert entered == 2  # 同项目只启动一次，不同项目同时进入 start。
    assert a.manager.is_started and b.manager.is_started
    await asyncio.gather(a.close(), b.close())

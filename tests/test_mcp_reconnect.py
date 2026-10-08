"""MCP 重连：面板 save/test 对失败池显式重试；stdio server 调用期崩溃即换代重载。"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import anyio
import pytest

from lumi.agents.tools.providers.mcp import pool
from lumi.gateway import mcp_rpc


class FailedManager:
    """已启动、首轮加载有 server 失败的假 manager（close 记到 closed）。"""

    is_started = True

    def __init__(self, closed: list[str]) -> None:
        self._closed = closed
        self.server_status = {"s": {"ok": False, "error": "连接超时（30s）"}}

    async def close(self) -> None:
        self._closed.append("closed")


def _failed_pool(monkeypatch, key: str) -> tuple[pool.McpPool, list[str]]:
    # 配置未变（hash 恒等）：此前 sync_config 零动作，失败终态永不重连
    closed: list[str] = []
    p = pool.McpPool(key)
    p.manager = FailedManager(closed)
    p.attempted_hash = "SAME"
    p.generation = 1
    monkeypatch.setattr(pool, "_pools", {key: p})
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda d: {"s": {}})
    monkeypatch.setattr(pool, "config_hash", lambda cfg: "SAME")
    return p, closed


async def test_panel_save_retries_failed_pool(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    p, closed = _failed_pool(monkeypatch, str(project.resolve()))

    await mcp_rpc._save(
        {"scope": "project", "project": str(project), "name": "s", "config": {}}
    )

    assert closed == ["closed"] and p.generation == 2


async def test_panel_test_success_retries_failed_pool(monkeypatch):
    p, closed = _failed_pool(monkeypatch, "__global__")

    async def ok(config):
        return {"ok": True}

    monkeypatch.setattr(mcp_rpc, "test_mcp_server", ok)
    await mcp_rpc._test({"config": {}})

    assert closed == ["closed"] and p.generation == 2


async def test_turn_start_refresh_still_does_not_retry(monkeypatch):
    # 轮首自查不是显式重试：失败终态照旧不轮轮重 spawn
    p, closed = _failed_pool(monkeypatch, "__global__")
    await pool.refresh_pool_config(None)
    assert closed == [] and p.generation == 1


_SERVER = """
import os, sys
from mcp.server.fastmcp import FastMCP
open(sys.argv[1], "w").write(str(os.getpid()))
m = FastMCP("t")

@m.tool()
def echo(x: str) -> str:
    return x

m.run()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")
async def test_stdio_crash_regenerates_pool(tmp_path, monkeypatch):
    # stdio server 被杀：调用抛空消息 ClosedResourceError，状态却仍 ok=True、池永不重载
    script, pid_file = tmp_path / "srv.py", tmp_path / "pid"
    script.write_text(_SERVER)
    cfg = {"s": {"transport": "stdio", "command": sys.executable}}
    cfg["s"]["args"] = [str(script), str(pid_file)]
    monkeypatch.setattr(pool, "_pools", {})
    monkeypatch.setattr(pool, "load_merged_mcp_config", lambda d: cfg)

    p = pool.pool_for(None)
    await p.wait_ready()
    old_manager, gen = p.manager, p.generation
    (tool,) = old_manager.get_tools()
    try:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
        await asyncio.sleep(0.3)
        with pytest.raises(anyio.ClosedResourceError):
            await tool.ainvoke({"x": "hi"})

        assert p.manager is not old_manager and p.generation > gen  # 已换代
        await p.wait_ready()  # 下一轮自然重载
        (tool,) = p.manager.get_tools()
        assert "hi" in str(await tool.ainvoke({"x": "hi"}))
    finally:
        await p.close()

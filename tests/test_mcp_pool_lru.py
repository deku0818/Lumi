"""MCP 池 LRU 记账：活跃会话每轮都算「用过」。"""

from __future__ import annotations

from lumi.agents.tools.providers import mcp
from lumi.agents.tools.providers.mcp import pool


async def test_turn_start_counts_as_use(isolated_config, tmp_path, monkeypatch):
    # 回归：last_used 只在建工具列表时更新，而 bridge 只在换代时才重建——一直在用的
    # 项目池会被当成「最久未用」淘汰，正在跑的会话工具断掉
    monkeypatch.setattr(pool, "_pools", {})
    p = mcp.pool_for(tmp_path)
    p.last_used = 0.0
    await pool.refresh_pool_config(tmp_path)
    assert p.last_used > 0

"""MCP 工具提供者 - 从 MCP 服务器加载工具（分层配置：全局 ∪ 会话项目）。

子模块：``config``（两层合并 + mtime 缓存）、``procs``（子进程树）、``pool``
（持久会话 + 按项目分池）、``probe``（连接测试）。本模块是 provider 入口。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools.structured import StructuredTool

from lumi.agents.tools.providers.mcp.config import (
    global_mcp_config_path,
    load_merged_mcp_config,
    project_mcp_config_path,
)
from lumi.agents.tools.providers.mcp.pool import (
    close_all_pools,
    get_pool_status,
    invalidate_mcp_pools,
    pool_for,
    pool_generation,
    project_wire_key,
    refresh_pool_config,
    set_on_pool_loaded,
)
from lumi.agents.tools.providers.mcp.probe import test_mcp_server
from lumi.utils.logger import logger

__all__ = [
    "close_all_pools",
    "get_mcp_tools",
    "get_pool_status",
    "global_mcp_config_path",
    "project_mcp_config_path",
    "invalidate_mcp_pools",
    "pool_for",
    "pool_generation",
    "project_wire_key",
    "refresh_pool_config",
    "set_on_pool_loaded",
    "test_mcp_server",
]

# 当前会话项目根：get_tools(project_dir=...) 进入时 set，get_mcp_tools 未显式传参时读它。
_current_project_dir: ContextVar[Path | None] = ContextVar(
    "lumi_mcp_project_dir", default=None
)


async def get_mcp_tools(
    filter_names: list[str] | None = None,
    project_dir: Path | None = None,
) -> list[StructuredTool]:
    """获取MCP服务器提供的工具（分层配置：全局 ∪ 会话项目）。

    ``project_dir`` 未显式给定时读 contextvar（由 ``get_tools`` 设置）；缺省即纯全局。
    每个项目一个会话池，池内首次加载后缓存工具；自动为 stdio 服务器创建持久会话。
    本函数恒不阻塞；需要等冷池就位的调用方经 ``get_tools(wait_mcp=True)`` 先等池。
    """
    if project_dir is None:
        project_dir = _current_project_dir.get()

    # 先登记池对象（轻量、不触发加载）再查配置：无配置的项目也要在 _pools 挂名，
    # 否则用户添加首个 server 时 invalidate 找不到池、无从换代
    pool = pool_for(project_dir)
    pool.last_used = time.monotonic()  # LRU 记账

    if not load_merged_mcp_config(project_dir):
        return []

    if not pool.manager.is_started:
        # 后台加载、立即返回空集：MCP 从不阻塞会话就绪/轮次（对齐 Claude Code 的
        # pending 语义）。就位后 generation 变化，会话在轮首重建工具列表。
        pool.ensure_loading()
        logger.warning("[MCP] 池加载中，本轮无 MCP 工具（下一轮自愈）: %s", pool.key)
        return []

    tools = pool.manager.get_tools()
    if filter_names:
        return [t for t in tools if t.name in filter_names]
    return tools

"""MCP 连接测试：用给定配置临时建一次会话，握手后枚举能力，随即断开。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, nullcontext
from typing import Any

from langchain_mcp_adapters import sessions

from lumi.agents.tools.providers.mcp.config import normalize_server_config
from lumi.agents.tools.providers.mcp.pool import (
    format_exception_details,
    start_lock,
)


async def _list_all_pages(
    list_page: Callable[..., Awaitable[Any]], attr: str
) -> list[Any]:
    """按 MCP 分页协议取全量：循环 cursor 直到 nextCursor 为空。"""
    items: list[Any] = []
    cursor: str | None = None
    while True:
        page = await list_page(cursor=cursor)
        items.extend(getattr(page, attr))
        if not page.nextCursor:
            return items
        cursor = page.nextCursor


async def _probe_mcp_server(config: dict[str, Any], timeout: float) -> dict[str, Any]:
    """建一次会话完成握手并枚举能力（tools/prompts/resources 按声明的 capability 取）。

    超时从拿到 spawn 锁才起表：后台池加载可长时间持锁（30s/server 串行），
    把排队时间计入预算会把健康 server 误报成超时。
    """
    async with asyncio.timeout(None) as probe_timeout:
        async with AsyncExitStack() as stack:
            # stdio spawn 子进程须与池 start 的 PID 快照互斥（diff 归属正确性依赖快照期间
            # 无别处 spawn），只锁 spawn 一瞬；HTTP/SSE 无子进程不加锁
            guard = start_lock if config.get("transport") == "stdio" else nullcontext()
            async with guard:
                probe_timeout.reschedule(asyncio.get_running_loop().time() + timeout)
                start = time.monotonic()
                session = await stack.enter_async_context(
                    sessions.create_session(config)
                )
            init = await session.initialize()
            latency_ms = int((time.monotonic() - start) * 1000)
            caps = init.capabilities

            tools = (
                [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.inputSchema,
                    }
                    for t in await _list_all_pages(session.list_tools, "tools")
                ]
                if caps.tools is not None
                else []
            )
            prompts = (
                [
                    {
                        "name": p.name,
                        "description": p.description or "",
                        "arguments": [
                            {
                                "name": a.name,
                                "description": a.description or "",
                                "required": bool(a.required),
                            }
                            for a in (p.arguments or [])
                        ],
                    }
                    for p in await _list_all_pages(session.list_prompts, "prompts")
                ]
                if caps.prompts is not None
                else []
            )
            resources = (
                [
                    {
                        "uri": str(r.uri),
                        "name": r.name or "",
                        "description": r.description or "",
                        "mime_type": r.mimeType or "",
                    }
                    for r in await _list_all_pages(session.list_resources, "resources")
                ]
                if caps.resources is not None
                else []
            )

    return {
        "ok": True,
        "server": {"name": init.serverInfo.name, "version": init.serverInfo.version},
        "latency_ms": latency_ms,
        "tools": tools,
        "prompts": prompts,
        "resources": resources,
    }


async def test_mcp_server(
    server_config: dict[str, Any], timeout: float = 15.0
) -> dict[str, Any]:
    """连接测试：用给定配置临时建一次会话，握手后枚举能力，随即断开。

    超时独立于池加载的 30s 上限：这是交互路径，用户在弹窗前实时等待，
    后台池加载放宽的理由不适用。计时从拿到 spawn 锁开始（见 _probe_mcp_server）。

    与常驻会话池完全独立——验证的是「这份配置能不能连上、有什么能力」，
    不动任何已建立的池。配置归一化与加载侧同源（:func:`normalize_server_config`），
    测试通过 = 会话加载也认。成功返回 ``{ok, server, latency_ms, tools, prompts,
    resources}``，失败返回 ``{ok: False, error}``。
    """
    config = normalize_server_config(server_config)
    try:
        return await _probe_mcp_server(config, timeout)
    except TimeoutError:
        return {"ok": False, "error": f"连接超时（{timeout:g}s）"}
    except Exception as e:
        return {"ok": False, "error": format_exception_details(e)}

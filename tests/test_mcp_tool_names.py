"""MCP 名称隔离：两种加载路径均保留服务端原名和参数注入。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_mcp_adapters import client, tools
from langgraph.prebuilt import ToolRuntime
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from lumi.agents.tools.providers.filesystem import read
from lumi.agents.tools.providers.mcp import pool
from lumi.agents.tools.registry import _deduplicate
from lumi.utils.config.models import ToolArgsConfig


@pytest.mark.parametrize("transport", ["stdio", "streamable_http", "sse"])
async def test_namespaced_tools_keep_original_mcp_calls(monkeypatch, transport):
    config = {
        name: {"transport": transport, "command": name}
        if transport == "stdio"
        else {"transport": transport, "url": name}
        for name in ("alpha-server", "beta")
    }
    sessions = {
        name: SimpleNamespace(
            initialize=AsyncMock(),
            list_tools=AsyncMock(
                return_value=ListToolsResult(
                    tools=[
                        Tool(
                            name="read",
                            description="Read from this server",
                            inputSchema={
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            },
                        )
                    ]
                )
            ),
            call_tool=AsyncMock(
                return_value=CallToolResult(
                    content=[TextContent(type="text", text=name)]
                )
            ),
        )
        for name in config
    }

    @asynccontextmanager
    async def create_session(connection, **kwargs):
        yield sessions[connection.get("command") or connection["url"]]

    monkeypatch.setattr(client, "create_session", create_session)
    monkeypatch.setattr(tools, "create_session", create_session)
    monkeypatch.setattr(
        pool,
        "get_config",
        lambda: SimpleNamespace(
            config=SimpleNamespace(tool_args=ToolArgsConfig(tenant=["read"]))
        ),
    )
    runtime = ToolRuntime(
        state={},
        context=None,
        config={"configurable": {"tool_args": {"tenant": "test-tenant"}}},
        stream_writer=lambda _: None,
        tool_call_id="call",
        store=None,
    )
    manager = pool.MCPSessionManager()
    try:
        loaded = await manager.start(config)
        assert [t.name for t in _deduplicate([read, *loaded])] == [
            "read",
            "mcp__alpha-server__read",
            "mcp__beta__read",
        ]
        for name, tool in zip(config, loaded, strict=True):
            result = await tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": "call",
                    "name": tool.name,
                    "args": {"query": "hello", "runtime": runtime},
                }
            )
            assert result.name == tool.name
            assert name in str(result.content)
            sessions[name].call_tool.assert_awaited_once_with(
                "read",
                {"query": "hello", "tenant": "test-tenant"},
                progress_callback=None,
            )
    finally:
        await manager.close()

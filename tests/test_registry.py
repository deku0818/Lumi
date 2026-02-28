"""ToolRegistry 测试"""

import types

from langchain_core.tools import tool as langchain_tool

from lumi.agents.tools.registry import ToolRegistry


def test_instance_singleton():
    a = ToolRegistry.instance()
    b = ToolRegistry.instance()
    assert a is b


async def test_register_module_provider():
    # 创建一个假模块，包含 StructuredTool
    @langchain_tool
    def dummy_tool(x: str) -> str:
        """A dummy tool"""
        return x

    mod = types.ModuleType("fake_mod")
    mod.dummy_tool = dummy_tool

    registry = ToolRegistry.instance()
    ToolRegistry.register("test_mod", mod)
    tools = await registry.get_tools()
    names = [t.name for t in tools]
    assert "dummy_tool" in names


async def test_register_async_function_provider():
    @langchain_tool
    def another_tool(x: str) -> str:
        """Another tool"""
        return x

    async def provider(names=None):
        tools = [another_tool]
        if names:
            tools = [t for t in tools if t.name in names]
        return tools

    registry = ToolRegistry.instance()
    ToolRegistry.register("test_async", provider)
    tools = await registry.get_tools()
    names = [t.name for t in tools]
    assert "another_tool" in names


async def test_get_tools_with_names_filter():
    @langchain_tool
    def tool_a(x: str) -> str:
        """Tool A"""
        return x

    @langchain_tool
    def tool_b(x: str) -> str:
        """Tool B"""
        return x

    mod = types.ModuleType("multi_mod")
    mod.tool_a = tool_a
    mod.tool_b = tool_b

    registry = ToolRegistry.instance()
    ToolRegistry.register("multi", mod)
    tools = await registry.get_tools(names=["tool_a"])
    names = [t.name for t in tools]
    assert "tool_a" in names
    assert "tool_b" not in names


async def test_provider_failure_graceful():
    @langchain_tool
    def good_tool(x: str) -> str:
        """Good tool"""
        return x

    mod = types.ModuleType("good_mod")
    mod.good_tool = good_tool

    async def bad_provider(names=None):
        raise RuntimeError("Provider exploded")

    registry = ToolRegistry.instance()
    ToolRegistry.register("good", mod)
    ToolRegistry.register("bad", bad_provider)
    tools = await registry.get_tools()
    # good provider 的工具仍然可用
    names = [t.name for t in tools]
    assert "good_tool" in names

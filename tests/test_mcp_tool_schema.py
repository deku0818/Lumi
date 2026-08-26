"""MCP 工具 input_schema 归一：顶层组合式 schema 在加载期压平成 object。

背景：Anthropic 系 API 硬性要求工具 input_schema 顶层 ``type: object``，个别 MCP
server 直接吐 anyOf/oneOf/allOf，整个请求被 400 拒收——一个坏工具连累该机器上所有
会话（含定时任务）。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from lumi.agents.tools.providers.mcp import (
    MCPSessionManager,
    flatten_top_level_combinators,
)


async def _noop(**_kwargs) -> str:
    return ""


def test_clean_schema_returned_unchanged() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }

    # 同一个对象：调用方据此判断「没动过」，不打无谓的告警
    assert flatten_top_level_combinators(schema) is schema


def test_top_level_any_of_becomes_object() -> None:
    flat = flatten_top_level_combinators(
        {
            "description": "地点检索",
            "anyOf": [
                {"type": "object", "properties": {"city": {"type": "string"}}},
                {"type": "object", "properties": {"lat": {"type": "number"}}},
            ],
        }
    )

    assert flat["type"] == "object"
    assert "anyOf" not in flat
    assert set(flat["properties"]) == {"city", "lat"}  # 各分支 properties 并集
    assert flat["description"] == "地点检索"  # 组合词之外的键原样保留


def test_required_keeps_only_fields_every_branch_requires() -> None:
    flat = flatten_top_level_combinators(
        {
            "oneOf": [
                {
                    "properties": {
                        "ak": {"type": "string"},
                        "city": {"type": "string"},
                    },
                    "required": ["ak", "city"],
                },
                {
                    "properties": {"ak": {"type": "string"}, "lat": {"type": "number"}},
                    "required": ["ak", "lat"],
                },
            ]
        }
    )

    # ak 两个分支都要 → 保留；city/lat 各只有一支要 → 不能强制
    assert flat["required"] == ["ak"]


def test_single_branch_all_of_keeps_its_required() -> None:
    flat = flatten_top_level_combinators(
        {"allOf": [{"properties": {"q": {"type": "string"}}, "required": ["q"]}]}
    )

    assert flat["required"] == ["q"]


def test_no_required_key_when_nothing_stays_required() -> None:
    flat = flatten_top_level_combinators(
        {
            "anyOf": [
                {"properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"properties": {"b": {"type": "string"}}, "required": ["b"]},
            ]
        }
    )

    assert "required" not in flat


def test_own_required_survives_flattening() -> None:
    flat = flatten_top_level_combinators(
        {
            "required": ["token"],
            "properties": {"token": {"type": "string"}},
            "anyOf": [{"properties": {"a": {"type": "string"}}}],
        }
    )

    assert flat["required"] == ["token"]
    assert set(flat["properties"]) == {"token", "a"}


def test_register_tools_rewrites_bad_schema_in_place() -> None:
    manager = MCPSessionManager()
    bad = StructuredTool(
        name="place_search",
        description="d",
        args_schema={"anyOf": [{"properties": {"city": {"type": "string"}}}]},
        coroutine=_noop,
    )
    good = StructuredTool(
        name="weather",
        description="d",
        args_schema={"type": "object", "properties": {}},
        coroutine=_noop,
    )
    out: list[StructuredTool] = []

    manager._register_tools("baidu-maps", [bad, good], out)

    # 坏 schema 就地压平（发给模型的就是这份），好 schema 原样不动
    assert bad.args_schema["type"] == "object"
    assert "anyOf" not in bad.args_schema
    assert good.args_schema == {"type": "object", "properties": {}}
    assert out == [bad, good]

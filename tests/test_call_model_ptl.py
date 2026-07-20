"""PTL 反应式压缩测试：call_model 路由决策 + summarizer 的 PTL 强制压缩分支。

mock chain / run_summary / get_config，不触发真实 LLM。验证：
- call_model 撞 PTL → Command(goto="Summarizer", update={"ptl_retry": True})
- ptl_retry 已置位再撞 PTL / 非 PTL 异常 → 原样 raise
- 成功响应清 ptl_retry
- summarizer PTL 分支：强制压缩产出 removes + carrier + 尾部换新 id 重加；
  熔断打开 / round 不足 / 无 SUMMARY prompt / 摘要失败 → 返回 {} 放行
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import PTLError, tool_loop_history
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages
from langgraph.types import Command

from lumi.agents.core import nodes
from lumi.agents.core.preprocessing.compact import (
    is_circuit_open,
    record_circuit_failure,
)

_TOKEN_CONFIG = SimpleNamespace(
    context_length=1000,
    summary_threshold=0.5,
    summary_failure_circuit_threshold=3,
    summary_circuit_reset_seconds=60,
    summary_ptl_retry_max=2,
    summary_ptl_retry_drop_ratio=0.3,
)


def _fake_config():
    return SimpleNamespace(
        config=SimpleNamespace(
            agents=SimpleNamespace(max_tokens=None),
            token=_TOKEN_CONFIG,
        ),
        load_prompt=lambda name: "SUMMARY PROMPT",
    )


_RUNTIME = SimpleNamespace(
    context=SimpleNamespace(
        tools=[],
        system_prompt="SYS",
        model_name="fake-model",
        effort=None,
        memory_enabled=True,
    )
)
_CONFIG = {"configurable": {"thread_id": "ptl-test"}}


# ─────────────────────────── call_model 路由决策 ───────────────────────────


async def _run_call_model(chain, state):
    with (
        patch.object(nodes, "tool_call_chain", return_value=chain),
        patch.object(nodes, "get_config", return_value=_fake_config()),
        patch.object(nodes, "detect_protocol", return_value="openai"),
    ):
        return await nodes.call_model(state, _RUNTIME)


async def test_ptl_routes_to_summarizer():
    chain = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=PTLError("prompt is too long"))
    )
    result = await _run_call_model(
        chain, {"messages": tool_loop_history(), "iterations": 1}
    )
    assert isinstance(result, Command)
    assert result.goto == "Summarizer"
    assert result.update == {"ptl_retry": True}


async def test_ptl_with_flag_set_raises_original():
    """刚压缩过仍超长：直接抛原错误，不再路由——每次 PTL 只换一次压缩机会"""
    chain = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=PTLError("prompt is too long"))
    )
    with pytest.raises(PTLError):
        await _run_call_model(
            chain, {"messages": tool_loop_history(), "iterations": 1, "ptl_retry": True}
        )


async def test_non_ptl_error_propagates():
    chain = SimpleNamespace(ainvoke=AsyncMock(side_effect=ValueError("boom")))
    with pytest.raises(ValueError):
        await _run_call_model(chain, {"messages": tool_loop_history(), "iterations": 1})


async def test_success_clears_ptl_retry():
    ok = AIMessage(content="ok", id="resp")
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=ok))
    result = await _run_call_model(
        chain, {"messages": tool_loop_history(), "iterations": 1, "ptl_retry": True}
    )
    assert result["ptl_retry"] is False
    assert result["messages"] == [ok]


async def test_success_without_flag_no_flag_update():
    ok = AIMessage(content="ok", id="resp")
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=ok))
    result = await _run_call_model(
        chain, {"messages": tool_loop_history(), "iterations": 1}
    )
    assert "ptl_retry" not in result


# ─────────────────────────── summarizer PTL 强制压缩分支 ───────────────────────────


async def _run_summarizer_ptl(messages, run_summary=None):
    with (
        patch.object(nodes, "get_config", return_value=_fake_config()),
        patch.object(
            nodes,
            "run_summary",
            new=run_summary or AsyncMock(return_value=("SUMMARY_TEXT", 0)),
        ),
    ):
        return await nodes.summarizer(
            {"messages": messages, "ptl_retry": True}, _RUNTIME, _CONFIG
        )


async def test_forced_compact_mid_tool_loop():
    messages = tool_loop_history()
    result = await _run_summarizer_ptl(messages)

    # 过真实 add_messages 断言合并后形态：[System, carrier, 尾部 2 round 新 id 副本]
    merged = add_messages(messages, result["messages"])
    assert isinstance(merged[0], SystemMessage)
    assert isinstance(merged[1], HumanMessage) and "<summary>" in merged[1].content
    assert [m.content for m in merged[2:]] == ["a2", "t2", "a3", "t3"]
    # 尾部换了新 id、tool_call_id 配对原样
    assert merged[2].id != "a2" and merged[2].tool_calls[0]["id"] == "tc2"
    assert merged[3].tool_call_id == "tc2"
    # ptl_retry 不在此清除（CallModel 成功后才清）
    assert "ptl_retry" not in result


async def test_forced_compact_insufficient_rounds_passes_through():
    messages = [
        HumanMessage(content="q", id="h"),
        AIMessage(content="a", id="a"),
        ToolMessage(content="t", tool_call_id="x", id="t"),
    ]
    run_summary = AsyncMock()
    result = await _run_summarizer_ptl(messages, run_summary=run_summary)
    assert result == {}
    run_summary.assert_not_awaited()


async def test_forced_compact_circuit_open_passes_through():
    for _ in range(3):
        record_circuit_failure("ptl-test", reset_sec=60)
    run_summary = AsyncMock()
    result = await _run_summarizer_ptl(tool_loop_history(), run_summary=run_summary)
    assert result == {}
    run_summary.assert_not_awaited()


async def test_forced_compact_summary_failure_passes_through_and_records_circuit():
    run_summary = AsyncMock(side_effect=RuntimeError("summary broke"))
    result = await _run_summarizer_ptl(tool_loop_history(), run_summary=run_summary)
    assert result == {}
    # 熔断计数 +1：再失败 2 次即打开
    record_circuit_failure("ptl-test", reset_sec=60)
    record_circuit_failure("ptl-test", reset_sec=60)
    assert is_circuit_open("ptl-test", threshold=3, reset_sec=60)


# ─────────────────────────── 整图回路 ───────────────────────────


async def test_full_graph_ptl_roundtrip():
    """真实拓扑走完整回路：CallModel PTL → Command 路由回 Summarizer 强制压缩
    → PreprocessMessages → CallModel 重试成功 → OnAgentStop → END。

    锁两件事：Command(goto) 与 is_use_tool 条件边并集时不把 OnAgentStop 拉进
    PTL 路由步（守卫走 END 空分支）；压缩 update 与重试响应在 state 中的最终形态。
    """
    from lumi.agents.core.graph import LumiAgent
    from lumi.agents.core.state import LumiAgentContext

    ok = AIMessage(content="ok", id="resp")
    chain = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=[PTLError("prompt is too long"), ok])
    )
    with (
        patch.object(nodes, "tool_call_chain", return_value=chain),
        patch.object(nodes, "get_config", return_value=_fake_config()),
        patch.object(nodes, "detect_protocol", return_value="openai"),
        patch.object(
            nodes, "run_summary", new=AsyncMock(return_value=("SUMMARY_TEXT", 0))
        ),
    ):
        agent = LumiAgent()
        result = await agent.graph.ainvoke(
            {"messages": tool_loop_history(), "iterations": 1},
            context=LumiAgentContext(model_name="fake-model"),
        )

    assert chain.ainvoke.await_count == 2
    assert result["ptl_retry"] is False
    contents = [m.content for m in result["messages"]]
    # [System, carrier, 尾部 2 round, 重试响应]；头部历史已压缩
    assert contents[0] == "sys"
    assert "<summary>" in contents[1] and "SUMMARY_TEXT" in contents[1]
    assert contents[2:] == ["a2", "t2", "a3", "t3", "ok"]

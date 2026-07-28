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
from lumi.sessions.message_visibility import latest_human_ts
from lumi.utils.constants import LUMI_META_KEY


def _user_message(text: str, mid: str, *, ts: int) -> HumanMessage:
    """带 ts 的真实用户消息（bridge 的 _build_user_message 同形态）。"""
    return HumanMessage(
        content=text,
        id=mid,
        additional_kwargs={LUMI_META_KEY: {"items": [{"text": text}], "ts": ts}},
    )


def _tool_round(tag: str) -> list:
    """一个完整工具轮：AI(tool_use) + 配对 ToolMessage。"""
    return [
        AIMessage(
            content=tag, id=tag, tool_calls=[{"name": "r", "args": {}, "id": f"c{tag}"}]
        ),
        ToolMessage(content=f"t{tag}", tool_call_id=f"c{tag}", id=f"t{tag}"),
    ]


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

    # 过真实 add_messages 断言合并后形态：
    # [System, carrier, 正在被回答的提问, 尾部 2 round 新 id 副本]
    merged = add_messages(messages, result["messages"])
    assert isinstance(merged[0], SystemMessage)
    assert isinstance(merged[1], HumanMessage) and "<summary>" in merged[1].content
    # "q" 是模型正在回答的诉求，压缩不能把它删成摘要转述
    assert [m.content for m in merged[2:]] == ["q", "a2", "t2", "a3", "t3"]
    assert merged[2].id != "h"  # 换新 id 才排得到 carrier 之后
    # 尾部换了新 id、tool_call_id 配对原样
    assert merged[3].id != "a2" and merged[3].tool_calls[0]["id"] == "tc2"
    assert merged[4].tool_call_id == "tc2"
    # ptl_retry 不在此清除（CallModel 成功后才清）
    assert "ptl_retry" not in result


async def test_forced_compact_keeps_pending_human_mid_history():
    """当前提问不在历史开头时同样要保住：round 分组把它并进前一个 AI 的 round，
    工具循环长于 keep_rounds 就会被卷进 to_summarize。"""
    messages = [
        SystemMessage(content="sys", id="s"),
        _user_message("很早的问题", "h0", ts=1000),
        *_tool_round("a0"),
        _user_message("现在的问题", "h1", ts=2000),
        *_tool_round("a1"),
        *_tool_round("a2"),
        *_tool_round("a3"),
    ]
    result = await _run_summarizer_ptl(messages)

    merged = add_messages(messages, result["messages"])
    texts = [m.content for m in merged if isinstance(m, HumanMessage)]
    assert "现在的问题" in texts  # 原话仍在上下文里
    assert "很早的问题" not in texts  # 已答完的旧问题照常压掉
    assert latest_human_ts(merged) == 2.0  # dream 判活基线保住


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
        # 阈值门的分母要查 models.dev 目录：钉 0 走 _TOKEN_CONFIG 兜底，不让本用例
        # 依赖本机 ~/.lumi/cache 里恰好没有条目模糊匹配上 "fake-model"
        patch.object(nodes, "context_window", return_value=0),
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
    # [System, carrier, 当前提问, 尾部 2 round, 重试响应]；头部历史已压缩
    assert contents[0] == "sys"
    assert "<summary>" in contents[1] and "SUMMARY_TEXT" in contents[1]
    assert contents[2:] == ["q", "a2", "t2", "a3", "t3", "ok"]

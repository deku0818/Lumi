"""PTL 反应式压缩测试：call_model 路由决策 + compact 的 PTL 强制压缩分支。

mock chain / run_compact / get_config，不触发真实 LLM。验证：
- call_model 撞 PTL → 原样抛给节点级 error_handler
- error_handler 撞 PTL → Command(goto="Compact", update={"ptl_retry": True})
- ptl_retry 已置位再撞 PTL / 非 PTL 异常 → 原样 raise
- 成功响应清 ptl_retry
- compact PTL 分支：强制压缩产出 removes + carrier + 尾部换新 id 重加；
  熔断打开 / round 不足 / 无 COMPACT prompt / 摘要失败 → 返回 {} 放行
"""

from __future__ import annotations

import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import (
    PTLError,
    apply_messages_update,
    resolved,
    tool_loop_history,
)
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.errors import NodeError
from langgraph.types import Command

from lumi.agents.core import nodes
from lumi.agents.core.meta_message import latest_human_ts
from lumi.agents.core.preprocessing import compact
from lumi.agents.core.preprocessing.compact import (
    is_circuit_open,
    record_circuit_failure,
)
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
    compact_threshold=0.5,
    compact_failure_circuit_threshold=3,
    compact_circuit_reset_seconds=60,
    compact_ptl_retry_max=2,
    compact_ptl_retry_drop_ratio=0.3,
)


def _fake_config():
    return SimpleNamespace(
        config=SimpleNamespace(
            agents=SimpleNamespace(max_tokens=None),
            token=_TOKEN_CONFIG,
        ),
        load_prompt=lambda name: "COMPACT PROMPT",
    )


_RUNTIME = SimpleNamespace(
    execution_info=None,
    context=SimpleNamespace(
        tools=[],
        system_prompt="SYS",
        model_name="fake-model",
        provider="",
        effort=None,
        memory_enabled=True,
    ),
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


async def test_call_model_lets_ptl_raise_for_the_handler():
    """call_model 自己不再兜 PTL——错误交给节点级 error_handler 裁决。"""
    chain = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=PTLError("prompt is too long"))
    )
    with pytest.raises(PTLError):
        await _run_call_model(chain, {"messages": tool_loop_history()})


async def _handle(exc: BaseException, state: dict):
    return await nodes.on_call_model_error(
        state, NodeError(node="CallModel", error=exc)
    )


async def test_ptl_routes_to_compactr():
    result = await _handle(
        PTLError("prompt is too long"),
        {"messages": tool_loop_history()},
    )
    assert isinstance(result, Command)
    assert result.goto == "Compact"
    assert result.update == {"ptl_retry": True}


async def test_ptl_with_flag_set_raises_original():
    """刚压缩过仍超长：直接抛原错误，不再路由——每次 PTL 只换一次压缩机会"""
    exc = PTLError("prompt is too long")
    with pytest.raises(PTLError) as caught:
        await _handle(exc, {"messages": tool_loop_history(), "ptl_retry": True})
    assert caught.value is exc  # 原样抛出，不包一层


async def test_non_ptl_error_propagates():
    exc = ValueError("boom")
    with pytest.raises(ValueError) as caught:
        await _handle(exc, {"messages": tool_loop_history()})
    assert caught.value is exc


async def test_success_clears_ptl_retry():
    ok = AIMessage(content="ok", id="resp")
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=ok))
    result = await _run_call_model(
        chain, {"messages": tool_loop_history(), "ptl_retry": True}
    )
    assert result["ptl_retry"] is False
    assert result["messages"] == [ok]


async def test_success_without_flag_no_flag_update():
    ok = AIMessage(content="ok", id="resp")
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=ok))
    result = await _run_call_model(chain, {"messages": tool_loop_history()})
    assert "ptl_retry" not in result


# ─────────────────────────── compact PTL 强制压缩分支 ───────────────────────────


async def _run_compact_node_ptl(messages, run_compact=None):
    with (
        patch.object(nodes, "get_config", return_value=_fake_config()),
        patch.object(compact, "get_config", return_value=_fake_config()),
        patch.object(
            compact,
            "run_compact",
            new=run_compact or AsyncMock(return_value=("COMPACT_TEXT", 0)),
        ),
    ):
        return await nodes.compact(
            {"messages": messages, "ptl_retry": True}, _RUNTIME, _CONFIG
        )


async def test_forced_compact_mid_tool_loop():
    messages = tool_loop_history()
    result = await _run_compact_node_ptl(messages)

    # 过真实 messages 通道断言合并后形态：
    # [carrier, 正在被回答的提问, 尾部 2 round 新 id 副本]
    merged = apply_messages_update(messages, result["messages"])
    assert isinstance(merged[0], HumanMessage) and "<compact>" in merged[0].content
    # "q" 是模型正在回答的诉求，压缩不能把它删成摘要转述
    assert [m.content for m in merged[1:]] == ["q", "a2", "t2", "a3", "t3"]
    assert merged[1].id != "h"  # 换新 id 才排得到 carrier 之后
    # 尾部换了新 id、tool_call_id 配对原样
    assert merged[2].id != "a2" and merged[2].tool_calls[0]["id"] == "tc2"
    assert merged[3].tool_call_id == "tc2"
    # ptl_retry 不在此清除（CallModel 成功后才清）
    assert "ptl_retry" not in result


async def test_forced_compact_keeps_pending_human_mid_history():
    """当前提问不在历史开头时同样要保住：round 分组把它并进前一个 AI 的 round，
    工具循环长于 keep_rounds 就会被卷进 to_compact。"""
    messages = [
        _user_message("很早的问题", "h0", ts=1000),
        *_tool_round("a0"),
        _user_message("现在的问题", "h1", ts=2000),
        *_tool_round("a1"),
        *_tool_round("a2"),
        *_tool_round("a3"),
    ]
    result = await _run_compact_node_ptl(messages)

    merged = apply_messages_update(messages, result["messages"])
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
    run_compact = AsyncMock()
    result = await _run_compact_node_ptl(messages, run_compact=run_compact)
    assert result == {}
    run_compact.assert_not_awaited()


async def test_forced_compact_circuit_open_passes_through():
    for _ in range(3):
        record_circuit_failure("ptl-test", reset_sec=60)
    run_compact = AsyncMock()
    result = await _run_compact_node_ptl(tool_loop_history(), run_compact=run_compact)
    assert result == {}
    run_compact.assert_not_awaited()


async def test_forced_compact_failure_passes_through_and_records_circuit():
    run_compact = AsyncMock(side_effect=RuntimeError("compact broke"))
    result = await _run_compact_node_ptl(tool_loop_history(), run_compact=run_compact)
    assert result == {}
    # 熔断计数 +1：再失败 2 次即打开
    record_circuit_failure("ptl-test", reset_sec=60)
    record_circuit_failure("ptl-test", reset_sec=60)
    assert is_circuit_open("ptl-test", threshold=3, reset_sec=60)


# ─────────────────────────── 整图回路 ───────────────────────────


async def test_full_graph_ptl_roundtrip():
    """真实拓扑走完整回路：CallModel PTL → error_handler 路由回 Compact 强制
    压缩 → PreprocessMessages → CallModel 重试成功 → OnAgentStop → END。

    锁两件事：PTL 路由步**不**分发 Stop hooks（error_handler 的 Command 路由不触发
    条件边求值，OnAgentStop 全程只进一次——这是先前 is_use_tool 里那道 ptl_retry
    守卫在保的不变量，守卫已随 handler 移除，改由本断言把住）；压缩 update 与重试
    响应在 state 中的最终形态。
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
        patch.object(nodes, "resolve", return_value=resolved(0)),
        patch.object(compact, "get_config", return_value=_fake_config()),
        patch.object(
            compact, "run_compact", new=AsyncMock(return_value=("COMPACT_TEXT", 0))
        ),
    ):
        from lumi.agents.core import graph as graph_module

        stop_calls = []
        real_stop = graph_module.on_agent_stop

        @functools.wraps(real_stop)
        async def counting_stop(*args, **kwargs):
            stop_calls.append(1)
            return await real_stop(*args, **kwargs)

        with patch.object(graph_module, "on_agent_stop", counting_stop):
            agent = LumiAgent()
            result = await agent.graph.ainvoke(
                {"messages": tool_loop_history()},
                context=LumiAgentContext(model_name="fake-model"),
            )

    assert stop_calls == [1]  # PTL 路由步没把 OnAgentStop 拉进来
    assert chain.ainvoke.await_count == 2
    assert result["ptl_retry"] is False
    contents = [m.content for m in result["messages"]]
    # [carrier, 当前提问, 尾部 2 round, 重试响应]；头部历史已压缩
    assert "<compact>" in contents[0] and "COMPACT_TEXT" in contents[0]
    # 当前提问原话保住，且压缩删掉的上下文注入块已在它上面重建（本轮后续调用不缺 env）
    from lumi.agents.core.meta_message import visible_user_text

    assert visible_user_text(result["messages"][1]) == "q"
    assert "<env>" in str(contents[1])
    assert contents[2:] == ["a2", "t2", "a3", "t3", "ok"]

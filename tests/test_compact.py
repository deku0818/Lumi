"""summary 压缩辅助单元测试：round 分组 / PTL 截头重试 / 熔断器 / 图像剥离。

纯逻辑断言，不触发真实 LLM。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lumi.agents.core.preprocessing import compact
from lumi.agents.core.preprocessing.compact import (
    clear_all_circuits,
    is_circuit_open,
    is_ptl_error,
    record_circuit_failure,
    reset_circuit,
    split_into_rounds,
    strip_images_from_messages,
    summarize_with_ptl_retry,
    truncate_head_for_ptl_retry,
)


@pytest.fixture(autouse=True)
def _clean_circuits():
    clear_all_circuits()
    yield
    clear_all_circuits()


class _PTLError(Exception):
    status_code = 400


# ─────────────────────────── round 分组 / 截头 ───────────────────────────


def test_split_into_rounds_groups_by_aimessage():
    msgs = [
        HumanMessage(content="h0"),  # 前导组
        AIMessage(content="a1"),
        ToolMessage(content="t1", tool_call_id="x"),
        AIMessage(content="a2"),
    ]
    rounds = split_into_rounds(msgs)
    assert [len(r) for r in rounds] == [1, 2, 1]
    assert isinstance(rounds[1][0], AIMessage) and isinstance(rounds[1][1], ToolMessage)


def test_truncate_head_drops_head_round():
    msgs = [
        HumanMessage(content="h0"),
        AIMessage(content="a1"),
        HumanMessage(content="h1"),
        AIMessage(content="a2"),
        HumanMessage(content="h2"),
    ]  # 3 rounds: [h0], [a1,h1], [a2,h2]
    out = truncate_head_for_ptl_retry(msgs, drop_ratio=0.3)
    # drop 1 round → 保留后两组
    assert out is not None
    assert [m.content for m in out] == ["a1", "h1", "a2", "h2"]


def test_truncate_head_returns_none_when_single_round():
    assert truncate_head_for_ptl_retry([HumanMessage(content="h")], 0.3) is None


# ─────────────────────────── 图像剥离 ───────────────────────────


def test_strip_images_replaces_media_blocks():
    msgs = [
        HumanMessage(
            content=[
                {"type": "text", "text": "看图"},
                {"type": "image", "source": {"data": "BIGBASE64"}},
            ]
        )
    ]
    out = strip_images_from_messages(msgs)
    assert out[0].content == [
        {"type": "text", "text": "看图"},
        {"type": "text", "text": "[image]"},
    ]


def test_strip_images_leaves_text_only_untouched():
    msg = HumanMessage(content="纯文本")
    out = strip_images_from_messages([msg])
    assert out[0] is msg  # 无图消息不复制，原样放行


# ─────────────────────────── PTL 错误识别 ───────────────────────────


def test_is_ptl_error_matches_substring_and_status():
    assert is_ptl_error(_PTLError("Prompt is too long: 250000 tokens"))


def test_is_ptl_error_rejects_non_ptl():
    assert not is_ptl_error(_PTLError("some unrelated 400"))  # 无 PTL 子串
    assert not is_ptl_error(ValueError("prompt is too long"))  # 有子串但非 4xx 类型


# ─────────────────────────── PTL 截头重试 ───────────────────────────


async def test_summarize_retries_on_ptl_then_succeeds():
    msgs = [
        HumanMessage(content="h0"),
        AIMessage(content="a1"),
        HumanMessage(content="h1"),
        AIMessage(content="a2"),
        HumanMessage(content="h2"),
    ]
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(
        side_effect=[
            _PTLError("prompt is too long"),
            AIMessage(content="摘要"),
        ]
    )
    content, attempts = await summarize_with_ptl_retry(
        msgs, "PROMPT", chain, max_retry=3, drop_ratio=0.3
    )
    assert content == "摘要"
    assert attempts == 1
    assert chain.ainvoke.await_count == 2


async def test_summarize_non_ptl_error_raises_immediately():
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(side_effect=ValueError("boom"))
    with pytest.raises(ValueError):
        await summarize_with_ptl_retry(
            [HumanMessage(content="h")], "P", chain, max_retry=3, drop_ratio=0.3
        )
    assert chain.ainvoke.await_count == 1


async def test_summarize_raises_when_cannot_truncate_further():
    # 单 round 截不动 → PTL 直接抛出，不无限重试
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(side_effect=_PTLError("prompt is too long"))
    with pytest.raises(_PTLError):
        await summarize_with_ptl_retry(
            [HumanMessage(content="h")], "P", chain, max_retry=3, drop_ratio=0.3
        )
    assert chain.ainvoke.await_count == 1


# ─────────────────────────── 熔断器 ───────────────────────────


def test_circuit_opens_after_threshold():
    tid = "t1"
    assert not is_circuit_open(tid, threshold=3, reset_sec=600)
    record_circuit_failure(tid, 600)
    record_circuit_failure(tid, 600)
    assert not is_circuit_open(tid, threshold=3, reset_sec=600)  # 2 < 3
    record_circuit_failure(tid, 600)
    assert is_circuit_open(tid, threshold=3, reset_sec=600)  # 3 >= 3


def test_circuit_reset_clears_count():
    tid = "t2"
    for _ in range(3):
        record_circuit_failure(tid, 600)
    assert is_circuit_open(tid, 3, 600)
    reset_circuit(tid)
    assert not is_circuit_open(tid, 3, 600)


def test_circuit_expires_after_reset_seconds(monkeypatch):
    tid = "t3"
    clock = {"now": 1000.0}
    monkeypatch.setattr(compact.time, "time", lambda: clock["now"])
    for _ in range(3):
        record_circuit_failure(tid, reset_sec=600)
    assert is_circuit_open(tid, 3, 600)
    clock["now"] += 601  # 超过 reset 窗口
    assert not is_circuit_open(tid, 3, 600)

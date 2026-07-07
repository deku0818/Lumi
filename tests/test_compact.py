"""summary 压缩辅助单元测试：round 分组 / PTL 截头重试 / 熔断器 / 图像剥离。

纯逻辑断言，不触发真实 LLM。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from lumi.agents.core.preprocessing import compact
from lumi.agents.core.preprocessing.compact import (
    build_compacted_update,
    clear_all_circuits,
    is_circuit_open,
    is_ptl_error,
    record_circuit_failure,
    reset_circuit,
    select_for_compaction,
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


# ─────────────────────── summarizer 只注入摘要 ───────────────────────


def _human_text(msg) -> str:
    """取 HumanMessage 的全部文本（content 可能是 str 或 block 列表）。"""
    if isinstance(msg.content, str):
        return msg.content
    return "".join(b.get("text", "") for b in msg.content if isinstance(b, dict))


async def test_summarizer_emits_carrier_before_last_human(monkeypatch):
    """压缩产出独立摘要 carrier + 末条 Human 原样重排到 carrier 之后；
    上下文块不在此重注入——下游 PreprocessMessages 的 context_inject hook
    在压缩后的历史上全量重建。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    from langchain_core.messages import AIMessage, HumanMessage

    from lumi.agents.core import nodes

    messages = [
        HumanMessage(content="m1", id="h1"),
        AIMessage(content="a1", id="a1"),
        HumanMessage(content="m2", id="h2"),
        AIMessage(content="a2", id="a2"),
        HumanMessage(content="现在的问题", id="h3"),
    ]
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            tools=[], system_prompt="SYS", model_name="x", memory_enabled=True
        )
    )
    fake_config = SimpleNamespace(
        config=SimpleNamespace(
            token=SimpleNamespace(
                context_length=1000,
                summary_threshold=0.5,
                summary_failure_circuit_threshold=3,
                summary_circuit_reset_seconds=60,
                summary_ptl_retry_max=2,
                summary_ptl_retry_drop_ratio=0.3,
            )
        ),
        load_prompt=lambda name: "SUMMARY PROMPT",
    )
    with (
        patch.object(nodes, "get_config", return_value=fake_config),
        patch.object(nodes, "context_window_tokens", return_value=10**9),
        patch.object(
            nodes,
            "run_summary",
            new=AsyncMock(return_value=("SUMMARY_TEXT", 0)),
        ),
    ):
        result = await nodes.summarizer(
            {"messages": messages}, runtime, {"configurable": {"thread_id": "tm"}}
        )

    # 必须过真实 add_messages 断言合并后顺序：reducer 对「Remove + 同 id 重加」
    # 是原地更新回原位置，只看 update 列表顺序会漏掉 carrier 落到末尾的回归
    from langgraph.graph.message import add_messages

    merged = add_messages(messages, result["messages"])
    assert [type(m).__name__ for m in merged] == ["HumanMessage", "HumanMessage"]
    carrier, last = merged
    carrier_text = _human_text(carrier)
    assert "<summary>" in carrier_text and "SUMMARY_TEXT" in carrier_text
    assert "system-reminder" not in carrier_text  # 上下文块交给下游 hook 全量重建
    assert _human_text(last) == "现在的问题"  # 用户消息在 carrier 之后、内容原样
    assert last.id != "h3"  # 换新 id 才能真正 append 到 carrier 之后


# ---------------------------------------------------------------------------
# 离线强制压缩：消息重写纯函数（不跑摘要链、不碰 checkpoint）
# ---------------------------------------------------------------------------


def _conversation(pairs: int) -> list:
    """[System, H0, A0, H1, A1, …]，末条恒为干净 AIMessage。"""
    msgs: list = [SystemMessage(content="sys", id="s")]
    for i in range(pairs):
        msgs.append(HumanMessage(content=f"h{i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
    return msgs


def test_select_compacts_small_conversation():
    # 无大小门：哪怕只有一轮（body=[Human, AI]）也压
    selected = select_for_compaction(_conversation(1))
    assert selected is not None
    to_summarize, last = selected
    assert [m.content for m in to_summarize] == ["h0"]
    assert last.content == "a0"


def test_select_skips_when_nothing_to_summarize():
    # body 仅剩末条 AI（无可压消息）→ 不白跑摘要
    assert (
        select_for_compaction(
            [SystemMessage(content="s", id="s"), AIMessage(content="a", id="a")]
        )
        is None
    )


def test_select_skips_when_last_is_human():
    msgs = _conversation(5)
    msgs.append(HumanMessage(content="pending", id="pending"))
    assert select_for_compaction(msgs) is None


def test_select_skips_when_last_ai_has_tool_calls():
    msgs = _conversation(5)
    msgs[-1] = AIMessage(
        content="calling",
        id="a4",
        tool_calls=[{"name": "read", "args": {}, "id": "t1"}],
    )
    assert select_for_compaction(msgs) is None


def test_select_returns_body_and_last_ai():
    msgs = _conversation(5)  # 1 system + 10 body
    selected = select_for_compaction(msgs)
    assert selected is not None
    to_summarize, last = selected
    assert len(to_summarize) == 9  # body[:-1]
    assert isinstance(last, AIMessage) and last.content == "a4"


def test_build_update_removes_body_keeps_head_leaves_carrier():
    msgs = _conversation(5)
    to_summarize, last = select_for_compaction(msgs)
    update = build_compacted_update(to_summarize, last, "浓缩摘要")

    out = update["messages"]
    removes = [m for m in out if isinstance(m, RemoveMessage)]
    additions = [m for m in out if not isinstance(m, RemoveMessage)]

    # 整段 body（含末条 AI）都被删；头部 System 未被删
    removed_ids = {m.id for m in removes}
    assert removed_ids == {f"h{i}" for i in range(5)} | {f"a{i}" for i in range(5)}
    assert "s" not in removed_ids

    # 只追加单条摘要 carrier；下条用户消息到来时由 context_inject 全量重建上下文
    assert len(additions) == 1
    assert isinstance(additions[0], HumanMessage)
    assert "浓缩摘要" in additions[0].content

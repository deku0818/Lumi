"""summary 压缩辅助单元测试：round 分组 / PTL 截头重试 / 熔断器 / 图像剥离。

纯逻辑断言，不触发真实 LLM。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from conftest import PTLError, tool_loop_history
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages

from lumi.agents.core.meta_message import CTX_DIGEST_KEY, reminder_human_message
from lumi.agents.core.preprocessing import compact
from lumi.agents.core.preprocessing.compact import (
    build_compacted_update,
    find_pending_human,
    is_circuit_open,
    is_ptl_error,
    messages_have_media,
    record_circuit_failure,
    reset_circuit,
    select_for_compaction,
    select_for_ptl_compaction,
    split_into_rounds,
    strip_images_from_messages,
    summarize_with_ptl_retry,
    truncate_head_for_ptl_retry,
)
from lumi.agents.core.preprocessing.summary import build_summary_carrier
from lumi.sessions.message_visibility import latest_human_ts
from lumi.utils.constants import LUMI_META_KEY

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


# ─────────────────────────── PTL 反应式压缩选材 ───────────────────────────


def test_select_for_ptl_keeps_tail_rounds_excludes_system():
    msgs = tool_loop_history()  # rounds: [Human], [a0,t0], [a1,t1], [a2,t2], [a3,t3]
    selected = select_for_ptl_compaction(msgs, keep_rounds=2)
    assert selected is not None
    to_summarize, tail = selected
    assert [m.id for m in to_summarize] == ["h", "a0", "t0", "a1", "t1"]
    assert [m.id for m in tail] == ["a2", "t2", "a3", "t3"]
    # System 不在任何一侧（调用方原位保留）
    assert not any(isinstance(m, SystemMessage) for m in [*to_summarize, *tail])


def test_select_for_ptl_none_when_rounds_insufficient():
    # rounds = [Human], [AI+Tool] → 2 组 ≤ keep_rounds+1，无可压
    msgs = [
        HumanMessage(content="q", id="h"),
        AIMessage(content="a", id="a"),
        ToolMessage(content="t", tool_call_id="x", id="t"),
    ]
    assert select_for_ptl_compaction(msgs, keep_rounds=2) is None
    assert select_for_ptl_compaction([], keep_rounds=2) is None


def test_select_for_ptl_trailing_human_stays_in_tail():
    """本轮首个 CallModel 即 PTL：末条 Human 归入尾部最后一个 round"""
    msgs = [
        HumanMessage(content="q0", id="h0"),
        AIMessage(content="a0", id="a0"),
        AIMessage(content="a1", id="a1"),
        AIMessage(content="a2", id="a2"),
        HumanMessage(content="现在的问题", id="h1"),
    ]  # rounds: [h0], [a0], [a1], [a2,h1]
    selected = select_for_ptl_compaction(msgs, keep_rounds=2)
    assert selected is not None
    to_summarize, tail = selected
    assert [m.id for m in tail] == ["a1", "a2", "h1"]
    assert [m.id for m in to_summarize] == ["h0", "a0"]


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
    assert is_ptl_error(PTLError("Prompt is too long: 250000 tokens"))


def test_is_ptl_error_matches_bedrock_variant():
    """Bedrock 撞窗口的错误串经网关透传时也要能识别"""
    assert is_ptl_error(PTLError("Input is too long for requested model"))


def test_is_ptl_error_rejects_non_ptl():
    assert not is_ptl_error(PTLError("some unrelated 400"))  # 无 PTL 子串
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
            PTLError("prompt is too long"),
            AIMessage(content="摘要"),
        ]
    )
    content, attempts = await summarize_with_ptl_retry(
        msgs, "PROMPT", chain, max_retry=3, drop_ratio=0.3
    )
    assert content == "摘要"
    assert attempts == 1
    assert chain.ainvoke.await_count == 2


async def test_summarize_strips_images_before_truncating_on_ptl():
    # 首次带原图撞 PTL → 第一档缓解剥图（不截头），剥图后成功
    img_human = HumanMessage(
        content=[
            {"type": "text", "text": "看图"},
            {"type": "image", "source": {"data": "BIGBASE64"}},
        ]
    )
    msgs = [img_human, AIMessage(content="a1"), HumanMessage(content="h1")]
    seen: list = []

    async def _invoke(payload):
        work = payload["messages"]
        seen.append(work)
        if messages_have_media(work):  # 首次仍带图 → PTL
            raise PTLError("prompt is too long")
        return AIMessage(content="摘要")

    chain = AsyncMock()
    chain.ainvoke = AsyncMock(side_effect=_invoke)
    content, attempts = await summarize_with_ptl_retry(
        msgs, "PROMPT", chain, max_retry=3, drop_ratio=0.3
    )
    assert content == "摘要"
    assert attempts == 1  # 剥图算一次重试
    assert chain.ainvoke.await_count == 2
    # 第一次带原图（含 image block），第二次图已被剥为 [image] 文本占位
    assert messages_have_media(seen[0])
    assert not messages_have_media(seen[1])


def _capture_chain() -> tuple[object, dict]:
    """摘要链替身：把发出去的 messages 收进 captured，回一条固定摘要。"""
    captured: dict = {}

    class _Chain:
        async def ainvoke(self, payload):
            captured["messages"] = payload["messages"]
            return AIMessage(content="摘要")

    return _Chain(), captured


async def _run_summary(history: list, model_name: str = "claude-x") -> str:
    """跑一次 run_summary，固定住与本组测试无关的那些参数。"""
    text, _ = await compact.run_summary(
        history,
        "PROMPT",
        tools=[],
        system_prompt="SYS",
        model_name=model_name,
        max_retry=2,
        drop_ratio=0.3,
    )
    return text


async def test_run_summary_drops_dangling_tool_use():
    """上一轮工具执行中途被取消 → 落库的 AIMessage(tool_use) 没有配对结果，
    摘要链不经 PreprocessMessages 的清理，原样发出去会被 provider 400。"""
    chain, captured = _capture_chain()
    history = [
        HumanMessage(content="上一问", id="h0"),
        AIMessage(
            content="",
            id="dangling",
            tool_calls=[{"name": "bash", "args": {}, "id": "tc-orphan"}],
        ),
        HumanMessage(content="新问题", id="h1"),
    ]
    with patch("lumi.models.chain.tool_call_chain", return_value=chain):
        await _run_summary(history)
    assert [m.id for m in captured["messages"] if m.id] == ["h0", "h1"]


async def test_run_summary_transforms_media_for_non_anthropic():
    # OpenAI 协议模型：摘要首次调用须把 image block 转成 image_url，不漏发 Anthropic 原生格式
    img_human = HumanMessage(
        content=[
            {"type": "text", "text": "看图"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "B64"},
            },
        ]
    )
    chain, captured = _capture_chain()
    with (
        patch("lumi.models.chain.tool_call_chain", return_value=chain),
        patch(
            "lumi.agents.core.response.get_default_model_name",
            return_value="gpt-4o",
        ),
    ):
        text = await _run_summary([img_human], model_name="gpt-4o")
    assert text == "摘要"
    sent = captured["messages"][0].content
    assert {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "B64"},
    } not in sent
    assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in sent)


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
    chain.ainvoke = AsyncMock(side_effect=PTLError("prompt is too long"))
    with pytest.raises(PTLError):
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


def _user(text: str, mid: str, ts: int) -> HumanMessage:
    """带 ts 的真实用户消息（bridge 的 _build_user_message 同形态）。"""
    return HumanMessage(
        content=text,
        id=mid,
        additional_kwargs={LUMI_META_KEY: {"items": [{"text": text}], "ts": ts}},
    )


def test_select_compacts_small_conversation():
    # 无大小门：哪怕只有一轮（body=[Human, AI]）也压
    body = select_for_compaction(_conversation(1))
    assert [m.content for m in body] == ["h0", "a0"]


def test_select_skips_when_nothing_to_summarize():
    # body 仅剩末条 AI（无可压消息）→ 不白跑摘要
    assert (
        select_for_compaction(
            [SystemMessage(content="s", id="s"), AIMessage(content="a", id="a")]
        )
        is None
    )


def test_select_accepts_trailing_pending_human():
    """末条是「发了没等到回答」的用户消息（turn 中途崩掉）：可压，原话由 keep 保住。"""
    msgs = _conversation(5)
    msgs.append(HumanMessage(content="pending", id="pending"))
    body = select_for_compaction(msgs)
    assert body is not None and body[-1].id == "pending"
    assert find_pending_human(body) is body[-1]


def test_select_skips_when_last_is_synthetic_human():
    # 摘要 carrier / 后台通知（items: []）不是用户诉求，不构成可压末条
    msgs = _conversation(5)
    msgs.append(build_summary_carrier("往期摘要"))
    assert select_for_compaction(msgs) is None


def test_select_skips_when_last_ai_has_tool_calls():
    msgs = _conversation(5)
    msgs[-1] = AIMessage(
        content="calling",
        id="a4",
        tool_calls=[{"name": "read", "args": {}, "id": "t1"}],
    )
    assert select_for_compaction(msgs) is None


def test_select_returns_full_body():
    msgs = _conversation(5)  # 1 system + 10 body
    body = select_for_compaction(msgs)
    assert len(body) == 10  # 含末条 AI——调用方整段进摘要，不再漏掉最后一句回复
    assert isinstance(body[-1], AIMessage) and body[-1].content == "a4"


def test_build_update_removes_body_keeps_head_leaves_carrier():
    body = select_for_compaction(_conversation(5))
    update = build_compacted_update(body, [], "浓缩摘要")

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


def test_build_update_reattaches_pending_human_verbatim():
    """末条是未被回答的用户消息 → 原话重挂在 carrier 之后（换新 id 才排得过去）。"""
    msgs = [*_conversation(2), _user("我的问题", "pending", ts=9000)]
    body = select_for_compaction(msgs)
    update = build_compacted_update(body, [find_pending_human(body)], "浓缩摘要")

    merged = add_messages(msgs, update["messages"])
    assert [_human_text(m) for m in merged if isinstance(m, HumanMessage)][-2:] == [
        "<summary>\n浓缩摘要\n</summary>\n",
        "我的问题",
    ]
    assert merged[-1].id != "pending"


def test_carrier_inherits_ts_so_dream_liveness_survives_compaction():
    """压缩删光真人消息后 dream 判活基线不能归零（否则该会话对 dream 隐身）。"""
    msgs = [
        SystemMessage(content="sys", id="s"),
        _user("早上问的", "h0", ts=5000),
        AIMessage(content="a0", id="a0"),
        _user("下午问的", "h1", ts=9000),
        AIMessage(content="a1", id="a1"),
    ]
    assert latest_human_ts(msgs) == 9.0

    body = select_for_compaction(msgs)
    merged = add_messages(
        msgs, build_compacted_update(body, [], "浓缩摘要")["messages"]
    )
    assert [m.id for m in merged] == ["s", merged[-1].id]  # 只剩 System + carrier
    assert latest_human_ts(merged) == 9.0


def test_reattached_messages_drop_ctx_marker():
    """重挂的消息必须剥 ctx_digest：基线块随压缩删掉了，marker 幸存会让
    context_inject 以为「模型已知」而不再重建全量上下文。"""
    marked = HumanMessage(
        content="带 marker 的提问",
        id="h1",
        additional_kwargs={CTX_DIGEST_KEY: {"env": "abc"}, LUMI_META_KEY: {"ts": 7000}},
    )
    update = build_compacted_update(
        [HumanMessage(content="旧", id="h0"), marked], [marked], "摘要"
    )
    reattached = update["messages"][-1]
    assert CTX_DIGEST_KEY not in reattached.additional_kwargs
    assert reattached.additional_kwargs[LUMI_META_KEY] == {"ts": 7000}  # 其余原样


# ─────────────────── PTL 强制压缩：保住正在被回答的诉求 ───────────────────


def test_find_pending_human_stops_at_answered_turn():
    answered = [
        HumanMessage(content="q", id="h"),
        AIMessage(content="答完了", id="a"),
    ]
    assert find_pending_human(answered) is None
    # 工具循环中段：末条 Tool，上一问还没答完
    assert find_pending_human(tool_loop_history()).id == "h"
    # 合成消息不算诉求
    assert find_pending_human([build_summary_carrier("摘要")]) is None


def test_find_pending_human_sees_through_reminder_pullback():
    """结构化输出未按格式 / Stop hook remind：无 tool_calls 的 AI 之后追加 reminder
    再回 CallModel——那条 AI 不是终态，用户诉求仍未被回答。"""
    pulled_back = [
        HumanMessage(content="上一问", id="h0"),
        AIMessage(content="上一答", id="a0"),
        HumanMessage(content="现在的问题", id="h1"),
        AIMessage(content="没按格式输出", id="a1"),
        reminder_human_message("<system-reminder>请调用工具</system-reminder>"),
    ]
    assert find_pending_human(pulled_back).id == "h1"

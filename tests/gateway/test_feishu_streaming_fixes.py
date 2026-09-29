"""飞书流式卡片回归：换卡期间入队的刷新、aborted 收尾、建卡失败降级。

只桩飞书网络出口（建卡 / content 覆写 / settings / send_markdown），append / Throttle /
UpdateQueue / _enqueue_render / _rebuild_card / end 走真实代码。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.gateway.channels.feishu.streaming import TOOL_SPINNER_FRAMES

CHAT = "oc_room"


def _rig(monkeypatch, *, create=lambda chat_id, reply_to: "card_1", content_ok=True):
    """装配一个桩掉网络出口的 channel，返回 (streaming, ops, sent)。"""
    ch = FeishuChannel(FeishuChannelConfig())
    st = ch.streaming
    ops: list[tuple] = []
    sent: list[str] = []

    def fake_content(card_id, content, seq):
        ops.append(("content", card_id, content))
        return (True, 0) if content_ok else (False, 300309)

    def fake_settings(card_id, enabled, seq):
        ops.append(("settings", card_id, enabled))
        return content_ok

    async def fake_send(chat_id, text, reply_to=None, title="", template="", note=""):
        sent.append(text)
        return "mid"

    monkeypatch.setattr(st, "_create_streaming_card_sync", create)
    monkeypatch.setattr(st, "_stream_update_text_sync", fake_content)
    monkeypatch.setattr(st, "_set_streaming_mode_sync", fake_settings)
    monkeypatch.setattr(ch, "send_markdown", fake_send)
    return st, ops, sent


def _contents(ops: list[tuple]) -> list[str]:
    return [o[2] for o in ops if o[0] == "content"]


# ── 3. 换卡等待期间入队的刷新必须打到新卡 ──
async def test_render_queued_during_rebuild_targets_new_card(monkeypatch):
    """回归：入队时快照 card_id，换卡等待期间入队的刷新仍打旧卡 → 撞失效再换一张，
    第一张新卡被遗弃、永远停在「生成中」（还白耗一次重建配额）。"""
    created: list[str] = []
    gate = threading.Event()

    def slow_create(chat_id, reply_to):
        created.append(f"card_{len(created) + 2}")
        gate.wait(2.0)  # executor 线程里卡住，给测试留出「换卡等待期间入队」的窗口
        return created[-1]

    st, ops, _ = _rig(monkeypatch, create=slow_create)
    base = st._stream_update_text_sync
    monkeypatch.setattr(
        st,
        "_stream_update_text_sync",
        lambda cid, content, seq: (
            (False, 230002) if cid == "card_1" else base(cid, content, seq)
        ),
    )
    buf = st._buf(CHAT)
    buf.card_id = "card_1"

    buf.text = "第一段"
    st._enqueue_render(buf)  # 任务 A：card_1 已撤销 → 换卡，卡在建卡上
    while not created:
        await asyncio.sleep(0.01)
    buf.text = "第一段第二段"
    st._enqueue_render(buf)  # 任务 B：换卡等待期间入队
    gate.set()
    await st.end(CHAT, aborted=False)

    assert created == ["card_2"]
    assert ops[-1] == ("settings", "card_2", False)  # 新卡被正常关流
    assert _contents(ops)[-1] == "第一段第二段"


# ── 4. aborted 只决定「空正文」的占位 ──
async def test_aborted_flushes_unrendered_tail(monkeypatch):
    """回归：aborted 收尾跳过最后一刷，节流窗口里没刷出的尾部永远上不了卡。"""
    st, ops, _ = _rig(monkeypatch)
    await st.append(CHAT, "前半段", "m1")
    st.bufs[CHAT].text += "后半段"  # 节流尚未 fire
    await st.end(CHAT, aborted=True)
    assert _contents(ops)[-1] == "前半段后半段"
    assert ops[-1] == ("settings", "card_1", False)


async def test_aborted_clears_busy_status_line(monkeypatch):
    """回归：工具执行中 /stop，卡片定格在 spinner 状态行上。"""
    st, ops, _ = _rig(monkeypatch)
    await st.append(CHAT, "我先跑一下测试。", "m1")
    await st.tool_activity(CHAT, "start", "bash", "m1")
    await st.bufs[CHAT].queue.drain()
    await st.end(CHAT, aborted=True)
    assert _contents(ops)[-1] == "我先跑一下测试。"


@pytest.mark.parametrize(
    ("aborted", "mark"), [(True, "⏹ 已中止"), (False, "✅ 已完成")]
)
async def test_empty_body_placeholder_follows_aborted(monkeypatch, aborted, mark):
    """纯工具轮无正文：终态占位按 aborted 区分，不留 spinner。"""
    st, ops, _ = _rig(monkeypatch)
    await st.tool_activity(CHAT, "start", "bash", "m1")
    await st.end(CHAT, aborted=aborted)
    last = _contents(ops)[-1]
    assert last == mark
    assert not any(f in last for f in TOOL_SPINNER_FRAMES)


async def test_aborted_falls_back_when_card_never_created(monkeypatch):
    """回归：CardKit 不可用时 aborted 轮的已流出正文静默丢失。"""
    st, _ops, sent = _rig(monkeypatch, create=lambda chat_id, reply_to: None)
    await st.append(CHAT, "部分答案", "m1")
    await st.end(CHAT, aborted=True)
    assert sent == ["部分答案"]


async def test_aborted_falls_back_when_final_push_fails(monkeypatch):
    """回归：aborted 轮终态刷新失败时不降级，卡上只剩前半段。"""
    st, _ops, sent = _rig(monkeypatch, content_ok=False)
    await st.append(CHAT, "部分答案", "m1")
    await st.end(CHAT, aborted=True)
    assert sent == ["部分答案"]


# ── 9. 建卡失败不反复重试；reply 失败退回直投 ──
async def test_card_creation_failure_not_retried(monkeypatch):
    """回归：建卡失败后每个 append / tool_activity / reset 都再打一遍建卡。"""
    calls: list[str | None] = []

    def failing_create(chat_id, reply_to):
        calls.append(reply_to)
        return None

    st, _ops, sent = _rig(monkeypatch, create=failing_create)
    await st.append(CHAT, "第一段", "m1")
    await st.tool_activity(CHAT, "start", "bash", "m1")
    st.mark(CHAT)  # 新一次模型调用：reset 只回滚这之后的正文
    await st.reset(CHAT, "m1")
    await st.append(CHAT, "第二段", "m1")
    await st.end(CHAT, aborted=False)
    assert calls == ["m1"]
    assert sent == ["第一段第二段"]


def test_streaming_card_reply_failure_falls_back_to_direct_send(monkeypatch):
    """回归：reply 锚点失效时卡片已建却投递失败，成孤儿，整轮退化到降级发送。"""
    from lumi.gateway.channels.feishu import lark_call as lc_mod

    ch = FeishuChannel(FeishuChannelConfig())
    direct: list[str] = []
    monkeypatch.setattr(ch, "reply_message_sync", lambda mid, t, c: None)
    monkeypatch.setattr(
        ch, "send_message_sync", lambda rid, t, c: direct.append(rid) or "c"
    )
    fake_resp = SimpleNamespace(data=SimpleNamespace(card_id="card_1"))
    monkeypatch.setattr(lc_mod, "lark_call", lambda op, fn, level="warning": fake_resp)

    assert ch.streaming._create_streaming_card_sync(CHAT, "m_dead") == "card_1"
    assert direct == [CHAT]

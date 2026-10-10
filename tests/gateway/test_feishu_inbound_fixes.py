"""飞书入站：收件箱跑空者接手、妙记事件、@ 替换、富文本分段、附件落盘名。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from lumi.agents.core.inbox import Inbox
from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu import inbound as inb
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.gateway.channels.feishu.parse import (
    extract_post_text,
    resolve_mentions,
    safe_filename,
)
from lumi.sessions.thread_runs import thread_runs


class _Bridge:
    """入站路径用到的最小桥：收件箱用真实 Inbox。"""

    def __init__(self):
        self._inbox = Inbox()

    def list_commands(self):
        return []

    async def interject(
        self, content, attachments=None, message_id="", message_meta=None
    ):
        self._inbox.put(HumanMessage(content, id=message_id or None))

    def withdraw_interjection(self, message_id):
        return self._inbox.withdraw(message_id)

    def take_interjections(self):
        return self._inbox.take()

    def pending_interjections(self):
        return self._inbox.pending()

    def set_env_extra(self, lines):
        pass

    async def delete_thread(self, tid):
        pass

    def reject_pending(self):
        pass

    async def close(self):
        pass


def _channel(monkeypatch):
    ch = FeishuChannel(FeishuChannelConfig())
    bridge = _Bridge()

    async def fake_get(tid):
        ch.bridge_pool._locks.setdefault(tid, thread_runs.lock_for(tid))
        ch.bridge_pool._bridges.setdefault(tid, bridge)
        return bridge

    monkeypatch.setattr(ch.bridge_pool, "get", fake_get)
    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id, text, reply_to=None, title="", template="", note=""):
        sent.append((text, title))
        return "mid"

    monkeypatch.setattr(ch, "send_markdown", fake_send)
    return ch, sent


def _record_turns(ch, monkeypatch) -> list:
    """打桩 run_turn：插话轮取走收件箱，记 (消息正文, 是否登记为可 /stop 的用户轮)。"""
    ran: list = []

    async def fake_run_turn(_ch, bridge, *, chat_id, interjections=False, **kw):
        own = asyncio.current_task() in ch.bridge_pool.run_tasks.values()
        ran.append(([m.content for m in bridge.take_interjections()], own))

    monkeypatch.setattr(inb, "run_turn", fake_run_turn)
    return ran


# ── 1. 持锁期间进箱的消息：锁一空就由跑空者接手 ──


async def test_queued_messages_run_when_foreign_holder_releases(monkeypatch):
    # 回归：妙记轮 / 每日整理 / 删会话持锁时到达的消息，持锁者不取收件箱，消息搁浅到
    # 下一条消息才被捎带
    ch, _ = _channel(monkeypatch)
    ran = _record_turns(ch, monkeypatch)
    bridge = await ch.bridge_pool.get("t")
    lock = ch.bridge_pool.lock("t")
    await lock.acquire()
    first = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("A")))
    await asyncio.sleep(0)
    second = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("B")))
    await asyncio.sleep(0)
    # 忙时到达的消息只进收件箱，已有跑空者就不再派等锁者
    assert second.done() and not first.done()
    assert [m.content for m in bridge.pending_interjections()] == ["A", "B"]
    lock.release()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    # 同一轮按序发出，且登记为可被 /stop 取消的用户轮
    assert ran == [(["A", "B"], True)]


async def test_notification_turn_leaves_queued_messages_to_their_own_task(
    monkeypatch,
):
    # 回归：通知轮跑完后在 poller task 里接着跑积压的用户轮——/stop 停不了（误报
    # 「后台通知无法中断」），其他会话的通知 / 妙记也被它卡住
    ch, _ = _channel(monkeypatch)
    ran = _record_turns(ch, monkeypatch)
    fi = ch.inbound
    await ch.bridge_pool.get("t")
    ch.bridge_pool.chat_ids["t"] = "oc"
    gate = asyncio.Event()

    async def fake_notification_turn(bridge, thread_id, chat_id):
        await gate.wait()

    monkeypatch.setattr(fi, "_run_notification_turn", fake_notification_turn)
    monkeypatch.setattr(inb, "NOTIFICATION_POLL_INTERVAL", 0)
    queue = SimpleNamespace(is_empty=lambda: False, has_for=lambda tid: True)
    monkeypatch.setattr(
        inb, "get_task_registry", lambda: SimpleNamespace(notification_queue=queue)
    )
    poller = asyncio.create_task(fi.notification_loop())
    while not ch.bridge_pool.lock("t").locked():
        await asyncio.sleep(0)
    msg = asyncio.create_task(fi._admit("oc", "t", inb._Pending("排队中")))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.wait_for(msg, 2)
    poller.cancel()
    assert ran == [(["排队中"], True)]


async def test_clear_hands_queued_message_to_its_own_task(monkeypatch):
    # /clear 持锁窗口内到达的消息：清空完成后接着跑，不搁浅
    ch, _ = _channel(monkeypatch)
    ran = _record_turns(ch, monkeypatch)
    monkeypatch.setattr(inb, "delete_meta", lambda tid: None)
    await ch.bridge_pool.get("t")
    arrived: list[asyncio.Task] = []

    async def slow_delete(tid):
        arrived.append(
            asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("清空期间")))
        )
        await asyncio.sleep(0)

    monkeypatch.setattr(ch.bridge_pool._bridges["t"], "delete_thread", slow_delete)
    await ch.inbound._run_system_command("clear", "oc", "t", "m1")
    await asyncio.wait_for(arrived[0], 2)
    assert ran == [(["清空期间"], True)]


# ── 11 / 21. 妙记事件：建桥失败不反复重试；事件挂在池上，跨热重载保留 ──


async def test_minute_event_dropped_when_bridging_fails(monkeypatch):
    # 回归：建桥持续失败时事件留队，每个 2s 轮询都打一条完整 traceback
    ch, _ = _channel(monkeypatch)

    async def broken_get(tid):
        raise RuntimeError("建桥失败")

    monkeypatch.setattr(ch.bridge_pool, "get", broken_get)
    ch.bridge_pool.minute_events.append(inb._MinuteEvent("obcnTOK", "ou_new"))
    await ch.inbound._drain_minute_events()
    assert ch.bridge_pool.minute_events == []


def test_minute_events_survive_inbound_rebuild():
    # 回归：待处理妙记事件存在 inbound 上，配置热重载重建 inbound 即丢
    ch = FeishuChannel(FeishuChannelConfig())
    ch.bridge_pool.minute_events.append(inb._MinuteEvent("obcnTOK", "ou_me"))
    rebuilt = inb.FeishuInbound(ch)
    assert rebuilt.channel.bridge_pool.minute_events == [
        inb._MinuteEvent("obcnTOK", "ou_me")
    ]


# ── 15. @ 占位符：长 key 先换，免得 @_user_1 吃掉 @_user_10 的前缀 ──


def test_resolve_mentions_longer_key_first():
    mentions = [
        SimpleNamespace(key="@_user_1", name="张三"),
        SimpleNamespace(key="@_user_10", name="李四"),
    ]
    assert resolve_mentions("@_user_1 @_user_10", mentions) == "@张三 @李四"


# ── 16. 富文本：标题独占一行，段内片段直接相连，段间换行 ──


def test_extract_post_text_keeps_line_structure():
    content = {
        "zh_cn": {
            "title": "标题",
            "content": [
                [
                    {"tag": "text", "text": "/commit "},
                    {"tag": "text", "text": "--amend"},
                ],
                [{"tag": "text", "text": "跑测试"}],
            ],
        }
    }
    assert extract_post_text(content) == "标题\n/commit --amend\n跑测试"


# ── 18. 附件落盘名：用完整 file_key，不同文件同名不互相覆盖 ──


def test_safe_filename_distinct_for_keys_sharing_prefix():
    a = safe_filename("file_v3_00ab_1111", "报告.pdf")
    b = safe_filename("file_v3_00ab_2222", "报告.pdf")
    assert a != b


async def test_queued_message_does_not_start_turn_on_closing_pool(monkeypatch):
    # 积压者在等锁：池正被回收（停用 / 换项目 / 退出）时拿到锁也不再开新轮，
    # 否则 close_all 等锁超时后会在跑着的轮下面关掉 bridge
    ch, _ = _channel(monkeypatch)
    ran = _record_turns(ch, monkeypatch)
    await ch.bridge_pool.get("t")
    lock = ch.bridge_pool.lock("t")
    await lock.acquire()
    waiter = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("A")))
    await asyncio.sleep(0)
    closing = asyncio.create_task(ch.bridge_pool.close_all())
    await asyncio.sleep(0)
    lock.release()
    await asyncio.wait_for(asyncio.gather(waiter, closing), 6)
    assert ran == []


async def test_deleting_thread_cancels_active_and_discards_queued_messages(monkeypatch):
    ch, _ = _channel(monkeypatch)
    started = asyncio.Event()
    turns = []

    async def run_turn(_ch, bridge, **kwargs):
        turns.append([m.content for m in bridge.take_interjections()])
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(inb, "run_turn", run_turn)
    active = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("A")))
    await started.wait()
    # 轮在跑时到达：进收件箱即返回，等工具边界注入
    await ch.inbound._admit("oc", "t", inb._Pending("B"))
    bridge = ch.bridge_pool._bridges["t"]
    assert len(bridge.pending_interjections()) == 1
    async with thread_runs.lock_for("t").cancel_and_hold():
        assert bridge.pending_interjections() == []  # 删除丢弃未注入的消息
        assert not ch.bridge_pool.run_tasks and not ch.inbound._draining
    [result] = await asyncio.gather(active, return_exceptions=True)
    assert isinstance(result, asyncio.CancelledError)
    assert turns == [["A"]]
    ran = _record_turns(ch, monkeypatch)
    await ch.inbound._admit("oc", "t", inb._Pending("new"))
    assert ran == [(["new"], True)]


async def test_deleting_thread_invalidates_pending_minute_event(monkeypatch):
    ch, _ = _channel(monkeypatch)
    data = SimpleNamespace(
        event=SimpleNamespace(
            minute_token="token",
            subscriber_ids=[SimpleNamespace(open_id="ou_me")],
        ),
        header=SimpleNamespace(event_id="minute-delete"),
    )
    await ch.inbound.on_minute_generated(data)
    item = ch.bridge_pool.minute_events[0]
    async with item.run_lock.cancel_and_hold():
        pass
    await ch.inbound._drain_minute_events()
    assert not ch.bridge_pool.minute_events
    assert not ch.bridge_pool._bridges  # 不为删除前积压的事件重新建会话。


async def test_deletion_cancellation_does_not_requeue_synthetic_turn(monkeypatch):
    from lumi.sessions.thread_runs import preserve_poller

    ch, _ = _channel(monkeypatch)
    started = asyncio.Event()
    requeued = []

    async def run_turn(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(inb, "run_turn", run_turn)
    lock = thread_runs.lock_for("t-delete-notification")

    async def poller():
        async with preserve_poller(lock):
            await ch.inbound._run_synthetic_turn(
                None, "t-delete-notification", "oc", "hint", lambda: requeued.append(1)
            )
        return "alive"

    task = asyncio.create_task(poller())
    await started.wait()
    async with lock.cancel_and_hold():
        pass
    assert await task == "alive"
    assert not requeued

"""飞书入站：排队接手、直连切目录守卫、妙记事件、@ 替换、富文本分段、附件落盘名。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu import inbound as inb
from lumi.gateway.channels.feishu.channel import FeishuChannel
from lumi.gateway.channels.feishu.inbound import (
    extract_post_text,
    resolve_mentions,
    safe_filename,
)


class _Bridge:
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
        ch.bridge_pool._locks.setdefault(tid, asyncio.Lock())
        ch.bridge_pool._bridges.setdefault(tid, bridge)
        return bridge

    monkeypatch.setattr(ch.bridge_pool, "get", fake_get)
    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id, text, reply_to=None, title="", template="", note=""):
        sent.append((text, title))
        return "mid"

    monkeypatch.setattr(ch, "send_markdown", fake_send)
    return ch, sent


def _record_batches(ch, monkeypatch) -> list:
    ran: list = []

    async def fake_run_batch(_ch, bridge, chat_id, thread_id, batch):
        own = ch.bridge_pool.run_tasks.get(thread_id) is asyncio.current_task()
        ran.append(([m.text for m in batch], own))

    monkeypatch.setattr(ch.inbound, "_run_batch", fake_run_batch)
    return ran


# ── 1. 持锁期间入队的消息：锁一空就由自己的 task 接手 ──


async def test_queued_messages_run_when_foreign_holder_releases(monkeypatch):
    # 回归：妙记轮 / 每日整理 / 删会话持锁时入队的消息，持锁者不取队列，消息搁浅到
    # 下一条消息才被捎带，且排在新消息后面（顺序颠倒）
    ch, _ = _channel(monkeypatch)
    ran = _record_batches(ch, monkeypatch)
    await ch.bridge_pool.get("t")
    lock = ch.bridge_pool.lock("t")
    await lock.acquire()
    first = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("A")))
    await asyncio.sleep(0)
    second = asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("B")))
    await asyncio.sleep(0)
    lock.release()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    # 合并成一轮、顺序不变，且登记为可被 /stop 取消的用户轮
    assert ran == [(["A", "B"], True)]


async def test_notification_turn_leaves_queued_messages_to_their_own_task(
    monkeypatch,
):
    # 回归：通知轮跑完后在 poller task 里接着跑积压的用户轮——/stop 停不了（误报
    # 「后台通知无法中断」），其他会话的通知 / 妙记也被它卡住
    ch, _ = _channel(monkeypatch)
    ran = _record_batches(ch, monkeypatch)
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
    ran = _record_batches(ch, monkeypatch)
    monkeypatch.setattr(inb, "delete_meta", lambda tid: None)
    await ch.bridge_pool.get("t")
    arrived: list[asyncio.Task] = []

    async def slow_delete(tid):
        arrived.append(
            asyncio.create_task(ch.inbound._admit("oc", "t", inb._Pending("清空期间")))
        )
        await asyncio.sleep(0)

    monkeypatch.setattr(ch.bridge_pool._bridges["t"], "delete_thread", slow_delete)
    await ch.inbound._run_system_command("clear", "", "oc", "t", "m1")
    await asyncio.wait_for(arrived[0], 2)
    assert ran == [(["清空期间"], True)]


# ── 2. 直连切目录撞上在跑的轮：先停再开 ──


async def test_direct_switching_dir_while_busy_asks_to_stop(monkeypatch, tmp_path):
    # 回归：换目录会开新会话（sid 清空），但在跑的轮结束时把旧 sid 写回，静默续了
    # 另一个项目的旧会话
    ch, sent = _channel(monkeypatch)
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    new.mkdir()
    monkeypatch.setattr(inb, "relay_precheck", lambda: "")
    monkeypatch.setattr(
        inb, "binding_of", lambda tid: {"cwd": str(old), "session_id": "sid-old"}
    )
    written: list = []
    monkeypatch.setattr(inb, "update_binding", lambda tid, **kw: written.append(kw))
    await ch.bridge_pool.get("t")
    await ch.bridge_pool.lock("t").acquire()
    await ch.inbound._direct_enter(False, f"--dir {new}\n任务", "oc", "t", "m1")
    assert written == []
    assert "/stop" in sent[0][0]


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
                    {"tag": "text", "text": "/direct claude "},
                    {"tag": "text", "text": "--dir /tmp"},
                ],
                [{"tag": "text", "text": "跑测试"}],
            ],
        }
    }
    assert extract_post_text(content) == "标题\n/direct claude --dir /tmp\n跑测试"


# ── 18. 附件落盘名：用完整 file_key，不同文件同名不互相覆盖 ──


def test_safe_filename_distinct_for_keys_sharing_prefix():
    a = safe_filename("file_v3_00ab_1111", "报告.pdf")
    b = safe_filename("file_v3_00ab_2222", "报告.pdf")
    assert a != b


async def test_queued_message_does_not_start_turn_on_closing_pool(monkeypatch):
    # 积压者在等锁：池正被回收（停用 / 换项目 / 退出）时拿到锁也不再开新轮，
    # 否则 close_all 等锁超时后会在跑着的轮下面关掉 bridge
    ch, _ = _channel(monkeypatch)
    ran = _record_batches(ch, monkeypatch)
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

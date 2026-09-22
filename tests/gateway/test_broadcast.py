"""BroadcastHub 单元测试 —— 从 ws.py 模块全局提取后的回归网（重构计划 M2）。

覆盖：cron.running 即时广播、后台任务变更去抖合并、注销后不再投递、
cron 执行结果投递（ResultDelivery sink）、单条连接失败不影响其他连接，
以及 cron 执行直播的按 thread pub/sub（观测者登记 / 发布 / 注销 / 背压）。
"""

import asyncio
from datetime import datetime

from lumi.agents.cron.run_log import RunRecord
from lumi.gateway.broadcast import BroadcastHub


class _FakeChannel:
    """收集 send 帧的假传输（Channel）。"""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.frames.append(frame)


def _events_of(ch: _FakeChannel, event_type: str) -> list[dict]:
    return [f for f in ch.frames if f["params"]["type"] == event_type]


async def test_cron_job_status_broadcasts_running():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)

    runs = [
        {"job_id": "job-a", "thread_id": "cron-a", "started_at": "2026-07-23T00:00:00"},
        {"job_id": "job-b", "thread_id": "cron-b", "started_at": "2026-07-23T00:01:00"},
    ]
    hub.on_cron_job_status(runs)
    await asyncio.sleep(0.01)  # 让 fire-and-forget 广播 task 执行

    events = _events_of(ch, "cron.running")
    assert len(events) == 1
    assert events[0]["params"]["payload"]["runs"] == runs


async def test_bg_task_change_is_debounced():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)

    # 合并窗口（0.1s）内快速多次变更 → 只广播一次全量快照
    hub.on_bg_task_change()
    hub.on_bg_task_change()
    hub.on_bg_task_change()
    await asyncio.sleep(0.15)

    updates = _events_of(ch, "bg_tasks.update")
    assert len(updates) == 1
    assert "tasks" in updates[0]["params"]["payload"]


async def test_dirty_during_flush_triggers_followup():
    """flush 窗口内又来变更 → 补发一次，保证最终态送达。"""
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)

    hub.on_bg_task_change()
    await asyncio.sleep(0.12)  # 第一次 flush 已发
    hub.on_bg_task_change()
    await asyncio.sleep(0.12)  # 补发第二次

    assert len(_events_of(ch, "bg_tasks.update")) == 2


async def test_unregister_stops_delivery():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)
    hub.unregister(ch)

    hub.on_cron_job_status(["x"])
    await asyncio.sleep(0.01)

    assert ch.frames == []


async def test_mcp_status_only_reaches_bound_channels():
    """mcp.status 定向广播：注册时声明 mcp_key 的连接按池 key 匹配投递，
    未声明（如未来 IM channel）或绑定别的池的连接一律收不到。"""
    hub = BroadcastHub()
    bound, other, unbound = _FakeChannel(), _FakeChannel(), _FakeChannel()
    hub.register(bound, mcp_key=lambda: "/p/X")
    hub.register(other, mcp_key=lambda: "/p/Y")
    hub.register(unbound)

    hub.on_mcp_status({"project": "/p/X", "servers": []})
    await asyncio.sleep(0.01)

    assert len(_events_of(bound, "mcp.status")) == 1
    assert _events_of(other, "mcp.status") == []
    assert _events_of(unbound, "mcp.status") == []


class _BrokenChannel:
    """send 总是失败的假传输。"""

    async def send(self, frame: dict) -> None:
        raise ConnectionError("broken")


async def test_deliver_broadcasts_cron_result():
    """ResultDelivery sink：cron 跑完一次 → cron.result 广播给所有连接。"""
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)

    record = RunRecord(
        job_id="abc",
        job_name="每日总结",
        started_at=datetime(2026, 6, 10, 9, 0, 0),
        finished_at=datetime(2026, 6, 10, 9, 0, 1),
        status="success",
        duration_ms=1200,
        output_summary="done",
        thread_id="cron-xyz",
    )
    await hub.deliver(record, "done")

    payloads = [f["params"]["payload"] for f in _events_of(ch, "cron.result")]
    assert len(payloads) == 1
    assert payloads[0]["job_id"] == "abc"
    assert payloads[0]["status"] == "success"
    assert payloads[0]["duration_ms"] == 1200
    # 前端据 thread_id 按 run 追踪未读（看一条消一条）
    assert payloads[0]["thread_id"] == "cron-xyz"


async def test_broken_channel_does_not_block_others():
    """单条连接 send 抛错不影响同批其他连接，也不把它踢出后续广播
    （连接生死只由 register/unregister 管，一次背压不该永久剔除）。"""
    hub = BroadcastHub()
    ok = _FakeChannel()
    hub.register(_BrokenChannel())
    hub.register(ok)

    await hub.send_event("cron.running", {"runs": []})
    assert len(ok.frames) == 1
    await hub.send_event("cron.running", {"runs": []})
    assert len(ok.frames) == 2


async def test_deliver_skips_unregistered_channel():
    """注销后的连接收不到结果投递。"""
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.register(ch)
    hub.unregister(ch)

    record = RunRecord(
        job_id="x",
        job_name="job",
        started_at=datetime(2026, 6, 10, 9, 0, 0),
        finished_at=datetime(2026, 6, 10, 9, 0, 1),
        status="success",
        duration_ms=1,
        output_summary="out",
    )
    await hub.deliver(record, "out")
    assert ch.frames == []


# ── cron 执行直播：按 thread 的 pub/sub ──


async def test_publish_reaches_observer_in_order():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.add_observer("cron-x", ch)
    hub.publish_thread_event("cron-x", {"n": 1})
    hub.publish_thread_event("cron-x", {"n": 2})
    await asyncio.sleep(0.02)
    assert ch.frames == [{"n": 1}, {"n": 2}]


async def test_no_observer_is_noop():
    hub = BroadcastHub()
    hub.publish_thread_event("cron-x", {"n": 1})  # 不报错、不建任何东西
    assert not hub.has_observers("cron-x")


async def test_multi_observer_each_receives():
    hub = BroadcastHub()
    a, b = _FakeChannel(), _FakeChannel()
    hub.add_observer("cron-x", a)
    hub.add_observer("cron-x", b)
    hub.publish_thread_event("cron-x", {"n": 1})
    await asyncio.sleep(0.02)
    assert a.frames == [{"n": 1}]
    assert b.frames == [{"n": 1}]


async def test_add_observer_is_idempotent():
    """同一 channel 重复登记同一 thread 只建一个观测者，不会收到重复帧。"""
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.add_observer("cron-x", ch)
    hub.add_observer("cron-x", ch)
    hub.publish_thread_event("cron-x", {"n": 1})
    await asyncio.sleep(0.02)
    assert ch.frames == [{"n": 1}]


async def test_remove_channel_clears_all_threads():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.add_observer("cron-a", ch)
    hub.add_observer("cron-b", ch)
    hub.remove_observer_channel(ch)
    assert not hub.has_observers("cron-a")
    assert not hub.has_observers("cron-b")


async def test_removed_channel_stops_receiving():
    hub = BroadcastHub()
    ch = _FakeChannel()
    hub.add_observer("cron-x", ch)
    hub.remove_observer_channel(ch)
    hub.publish_thread_event("cron-x", {"n": 1})
    await asyncio.sleep(0.02)
    assert ch.frames == []


async def test_backpressure_never_blocks_publisher():
    """慢观测者（send 永不返回）时 publish 仍非阻塞：满即丢，发布方绝不背压。"""
    hub = BroadcastHub()

    class _StuckChannel:
        async def send(self, frame: dict) -> None:
            await asyncio.Event().wait()  # 永不返回，模拟卡死的连接

    hub.add_observer("cron-x", _StuckChannel())
    # 远超队列上限（256）的帧：全部 put_nowait / drop-oldest，无一阻塞。
    for i in range(2000):
        hub.publish_thread_event("cron-x", {"n": i})
    # 走到这里没卡死即证明发布不背压

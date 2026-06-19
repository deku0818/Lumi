"""BroadcastHub 单元测试 —— 从 ws.py 模块全局提取后的回归网（重构计划 M2）。

覆盖：cron.running 即时广播、后台任务变更去抖合并、注销后不再投递。
"""

import asyncio

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

    hub.on_cron_job_status(["job-a", "job-b"])
    await asyncio.sleep(0.01)  # 让 fire-and-forget 广播 task 执行

    events = _events_of(ch, "cron.running")
    assert len(events) == 1
    assert events[0]["params"]["payload"]["names"] == ["job-a", "job-b"]


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

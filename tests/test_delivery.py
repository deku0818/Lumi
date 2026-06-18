"""DeliveryManager 和 ResultDelivery 单元测试。"""

from __future__ import annotations

import asyncio

from datetime import datetime

from lumi.agents.cron.delivery import APIDelivery, DeliveryManager, ResultDelivery
from lumi.agents.cron.run_log import RunRecord


def _rec(
    job_name: str = "job",
    *,
    job_id: str = "",
    status: str = "success",
    started_at: datetime | None = None,
    duration_ms: int = 0,
) -> RunRecord:
    """构造测试用 RunRecord。"""
    started = started_at or datetime(2026, 1, 1, 0, 0, 0)
    return RunRecord(
        job_id=job_id,
        job_name=job_name,
        started_at=started,
        finished_at=started,
        status=status,
        duration_ms=duration_ms,
        output_summary="",
    )


class FakeDelivery(ResultDelivery):
    """测试用的假投递通道，记录收到的消息（含全部字段）。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.closed: bool = False

    async def deliver(self, record: RunRecord, text: str) -> None:
        self.messages.append(
            {
                "job_name": record.job_name,
                "output": text,
                "started_at": record.started_at,
                "duration_ms": record.duration_ms,
            }
        )

    async def close(self) -> None:
        self.closed = True


class FailingDelivery(ResultDelivery):
    """测试用的总是失败的投递通道。"""

    async def deliver(self, record: RunRecord, text: str) -> None:
        raise RuntimeError("投递失败")


class FailingCloseDelivery(ResultDelivery):
    """close 时抛异常的投递通道。"""

    async def deliver(self, record: RunRecord, text: str) -> None:
        pass

    async def close(self) -> None:
        raise RuntimeError("关闭失败")


# -- register / unregister --


async def test_register_and_broadcast() -> None:
    dm = DeliveryManager()
    ch = FakeDelivery()
    dm.register(ch)

    await dm.broadcast(_rec("job1"), "hello")

    assert len(ch.messages) == 1
    assert ch.messages[0]["job_name"] == "job1"
    assert ch.messages[0]["output"] == "hello"


async def test_unregister_removes_channel() -> None:
    dm = DeliveryManager()
    ch = FakeDelivery()
    dm.register(ch)
    dm.unregister(ch)

    await dm.broadcast(_rec("job1"), "hello")

    assert len(ch.messages) == 0


# -- broadcast 隔离 --


async def test_broadcast_isolates_channel_failure() -> None:
    """单个通道投递失败不影响其他通道。"""
    dm = DeliveryManager()
    ok = FakeDelivery()
    fail = FailingDelivery()
    ok2 = FakeDelivery()

    dm.register(ok)
    dm.register(fail)
    dm.register(ok2)

    await dm.broadcast(_rec("job1"), "result")

    assert len(ok.messages) == 1
    assert ok.messages[0]["job_name"] == "job1"
    assert len(ok2.messages) == 1
    assert ok2.messages[0]["job_name"] == "job1"


# -- close_all --


async def test_close_all_closes_channels_and_clears() -> None:
    dm = DeliveryManager()
    ch1 = FakeDelivery()
    ch2 = FakeDelivery()
    dm.register(ch1)
    dm.register(ch2)

    await dm.close_all()

    assert ch1.closed
    assert ch2.closed
    # close_all 后广播不再投递
    await dm.broadcast(_rec("job1"), "after-close")
    assert len(ch1.messages) == 0


async def test_close_all_tolerates_close_failure() -> None:
    """单个通道 close 失败不影响其他通道。"""
    dm = DeliveryManager()
    fail = FailingCloseDelivery()
    ok = FakeDelivery()
    dm.register(fail)
    dm.register(ok)

    await dm.close_all()

    assert ok.closed


# -- APIDelivery --


async def test_api_delivery_buffers_when_no_subscribers() -> None:
    """无订阅者时结果被缓存。"""
    ad = APIDelivery(max_buffer=5)

    await ad.deliver(_rec("job1"), "result1")
    await ad.deliver(_rec("job2"), "result2")

    assert len(ad._buffer) == 2
    assert ad._buffer[0]["job_name"] == "job1"
    assert ad._buffer[0]["output"] == "result1"


async def test_api_delivery_buffer_evicts_oldest_when_full() -> None:
    """缓存满时丢弃最旧的结果。"""
    ad = APIDelivery(max_buffer=2)

    await ad.deliver(_rec("job1"), "r1")
    await ad.deliver(_rec("job2"), "r2")
    await ad.deliver(_rec("job3"), "r3")

    assert len(ad._buffer) == 2
    assert ad._buffer[0]["job_name"] == "job2"
    assert ad._buffer[1]["job_name"] == "job3"


async def test_api_delivery_pushes_to_subscribers() -> None:
    """有订阅者时直接推送，不缓存。"""
    ad = APIDelivery()
    received: list[dict[str, str]] = []

    async def consume() -> None:
        async for msg in ad.subscribe():
            received.append(msg)
            if len(received) >= 2:
                break

    task = asyncio.create_task(consume())
    # 等待订阅者注册
    await asyncio.sleep(0.05)

    await ad.deliver(_rec("j1"), "o1")
    await ad.deliver(_rec("j2"), "o2")

    await asyncio.wait_for(task, timeout=2)

    assert len(received) == 2
    assert received[0]["job_name"] == "j1"
    assert received[0]["output"] == "o1"
    assert received[1]["job_name"] == "j2"
    assert received[1]["output"] == "o2"
    assert ad._buffer == []


async def test_api_delivery_subscriber_receives_buffered_first() -> None:
    """新订阅者先接收缓存结果，再接收新结果。"""
    ad = APIDelivery()

    # 先投递两条（无订阅者，进入缓存）
    await ad.deliver(_rec("old1"), "cached1")
    await ad.deliver(_rec("old2"), "cached2")

    received: list[dict[str, str]] = []

    async def consume() -> None:
        async for msg in ad.subscribe():
            received.append(msg)
            if len(received) >= 3:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    # 投递一条新的
    await ad.deliver(_rec("new1"), "live1")

    await asyncio.wait_for(task, timeout=2)

    assert len(received) == 3
    assert received[0]["job_name"] == "old1"
    assert received[1]["job_name"] == "old2"
    assert received[2]["job_name"] == "new1"
    # 缓存已清空
    assert ad._buffer == []


async def test_api_delivery_close_terminates_subscribers() -> None:
    """close 终止所有订阅者并清理资源。"""
    ad = APIDelivery()
    received: list[dict[str, str]] = []

    async def consume() -> None:
        async for msg in ad.subscribe():
            received.append(msg)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    assert len(ad._subscribers) == 1

    await ad.close()

    await asyncio.wait_for(task, timeout=2)

    assert ad._subscribers == []
    assert ad._buffer == []


async def test_api_delivery_multiple_subscribers() -> None:
    """多个订阅者同时接收消息。"""
    ad = APIDelivery()
    r1: list[dict[str, str]] = []
    r2: list[dict[str, str]] = []

    async def consume(target: list[dict[str, str]]) -> None:
        async for msg in ad.subscribe():
            target.append(msg)
            if len(target) >= 1:
                break

    t1 = asyncio.create_task(consume(r1))
    t2 = asyncio.create_task(consume(r2))
    await asyncio.sleep(0.05)

    await ad.deliver(_rec("shared"), "data")

    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)

    assert r1[0]["job_name"] == "shared"
    assert r1[0]["output"] == "data"
    assert r2[0]["job_name"] == "shared"
    assert r2[0]["output"] == "data"


# -- broadcast 传递 started_at / duration_ms --


async def test_broadcast_forwards_timing_metadata() -> None:
    """broadcast 将 started_at 和 duration_ms 正确传递到通道。"""
    dm = DeliveryManager()
    ch = FakeDelivery()
    dm.register(ch)

    ts = datetime(2026, 3, 7, 12, 0, 0)
    await dm.broadcast(_rec("job1", started_at=ts, duration_ms=1234), "result")

    assert len(ch.messages) == 1
    assert ch.messages[0]["started_at"] == ts
    assert ch.messages[0]["duration_ms"] == 1234


# -- APIDelivery 序列化 started_at / duration_ms --


async def test_api_delivery_serializes_timing_metadata() -> None:
    """APIDelivery 将 started_at 序列化为 ISO 格式字符串。"""
    ad = APIDelivery()

    ts = datetime(2026, 3, 7, 12, 0, 0)
    await ad.deliver(_rec("job1", started_at=ts, duration_ms=500), "out")

    assert len(ad._buffer) == 1
    assert ad._buffer[0]["started_at"] == "2026-03-07T12:00:00"
    assert ad._buffer[0]["duration_ms"] == 500


async def test_api_delivery_subscriber_receives_timing_metadata() -> None:
    """订阅者接收到序列化后的 started_at 和 duration_ms。"""
    ad = APIDelivery()
    received: list[dict] = []

    async def consume() -> None:
        async for msg in ad.subscribe():
            received.append(msg)
            if len(received) >= 1:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)

    ts = datetime(2026, 3, 7, 15, 30, 0)
    await ad.deliver(_rec("j1", started_at=ts, duration_ms=2000), "o1")

    await asyncio.wait_for(task, timeout=2)

    assert received[0]["started_at"] == "2026-03-07T15:30:00"
    assert received[0]["duration_ms"] == 2000

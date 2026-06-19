"""DeliveryManager 和 ResultDelivery 单元测试。"""

from __future__ import annotations

from datetime import datetime

from lumi.agents.cron.delivery import DeliveryManager, ResultDelivery
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

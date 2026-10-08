"""定时任务的时间一律带时区：远程 UTC 服务器 + 本地 UTC+8 客户端不差 8 小时。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from lumi.agents.cron.compensation import should_compensate
from lumi.agents.cron.models import Job, Schedule, ScheduleType
from lumi.agents.cron.run_log import RunRecord


def _at(value: str) -> Job:
    return Job(
        name="t", schedule=Schedule(type=ScheduleType.AT, value=value), prompt="p"
    )


def test_at_compensation_converts_offsets():
    # 此前直接抹掉时区不换算：未来 2h 的 ...Z 被当成已错过，过去 2h 的 +12:00 当成没错过
    now = datetime.now().astimezone()
    future_utc = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past_plus12 = (
        datetime.now(timezone(timedelta(hours=12))) - timedelta(hours=2)
    ).isoformat()
    assert should_compensate(_at(future_utc), now, None) is False
    assert should_compensate(_at(past_plus12), now, None) is True


def test_timestamps_are_aware_and_legacy_naive_reads_as_local():
    job = Job(
        name="t", schedule=Schedule(type=ScheduleType.INTERVAL, value="5m"), prompt="p"
    )
    assert job.created_at.tzinfo is not None
    legacy = {**job.to_dict(), "created_at": "2026-01-01T09:00:00"}
    assert Job.from_dict(legacy).created_at.tzinfo is not None
    rec = RunRecord.from_dict(
        {
            "job_id": "j",
            "job_name": "t",
            "started_at": "2026-01-01T09:00:00",
            "finished_at": "2026-01-01T09:00:01",
            "status": "success",
            "duration_ms": 1000,
            "output_summary": "",
            "error": "",
        }
    )
    assert rec.started_at.tzinfo is not None and rec.finished_at.tzinfo is not None


def test_relative_at_value_carries_offset():
    assert datetime.fromisoformat(Schedule.parse("+2h").value).tzinfo is not None


def test_past_at_time_is_rejected():
    # 过去的时间点会在创建当场执行并自删；工具描述里的示例本身就已过期
    import pytest

    with pytest.raises(ValueError, match="已过去"):
        Schedule.parse("2020-01-01T09:00:00")


async def test_unchanged_schedule_is_not_reregistered(tmp_path):
    """编辑任务只改名：表单恒回传 schedule，原样未变就不重新解析 / 注册（interval 不重锚；
    已过期的暂停一次性任务也能改名）。"""
    from unittest.mock import MagicMock

    from lumi.agents.cron.job_store import JobStore
    from lumi.agents.cron.service import CronService

    store = JobStore(tmp_path / "jobs.json")
    past = Job(
        name="t", schedule=Schedule(ScheduleType.AT, "2020-01-01T09:00:00"), prompt="p"
    )
    await store.upsert(past)
    scheduler = MagicMock()
    service = CronService(scheduler, store, MagicMock())
    await service.update(past.id, name="renamed", schedule_raw="2020-01-01T09:00:00")
    assert (await store.get(past.id)).name == "renamed"
    scheduler.add_job.assert_not_called()

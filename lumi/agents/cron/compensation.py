"""补偿判定：判断任务是否在离线期间错过执行、需补执行一次。

纯函数 ``should_compensate`` 根据任务的调度类型、当前时间与上次执行记录，
判定是否需要补偿。不触碰调度器状态，便于独立测试与复用。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lumi.agents.cron.models import Job, ScheduleType, parse_interval_to_seconds
from lumi.agents.cron.run_log import RunRecord


def should_compensate(job: Job, now: datetime, last_run: RunRecord | None) -> bool:
    """判断任务是否需要补偿执行。"""
    match job.schedule.type:
        case ScheduleType.AT:
            run_date = datetime.fromisoformat(job.schedule.value)
            # ISO 输入可能带时区偏移 → 统一转 naive 再与 naive now 比较（与 CRON 分支一致）
            if run_date.tzinfo is not None:
                run_date = run_date.replace(tzinfo=None)
            if run_date >= now:
                return False
            return last_run is None or last_run.status != "success"

        case ScheduleType.INTERVAL:
            interval_secs = parse_interval_to_seconds(job.schedule.value)
            if last_run is None:
                return (now - job.created_at).total_seconds() >= interval_secs
            expected_next = last_run.started_at + timedelta(seconds=interval_secs)
            return expected_next < now

        case ScheduleType.CRON:
            trigger = job.schedule.to_trigger()
            ref_time = last_run.started_at if last_run else job.created_at
            next_fire = trigger.get_next_fire_time(None, ref_time)
            if next_fire is None:
                return False
            # APScheduler 可能返回 aware datetime，统一转为 naive 比较
            if next_fire.tzinfo is not None:
                next_fire = next_fire.replace(tzinfo=None)
            return next_fire < now

    return False

"""Cron RPC：desktop WS 的定时任务管理方法实现。

运行时（CronRuntime）由 serve lifespan 通过 ``set_cron_runtime()`` 注入，
未注入（初始化失败）时所有方法抛 RuntimeError，由 WS 层转为 error 帧。
错误消息（如调度规则解析失败）直接面向前端展示。
"""

from __future__ import annotations

import asyncio

from lumi.agents.cron.models import Job
from lumi.agents.cron.runtime import CronRuntime
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.cron.service import CronService
from lumi.utils.constants import MAX_CRON_RUN_THREADS

CRON_METHODS = frozenset(
    {
        "list_cron_jobs",
        "create_cron_job",
        "update_cron_job",
        "delete_cron_job",
        "toggle_cron_job",
        "run_cron_job",
        "stop_cron_run",
        "list_cron_runs",
    }
)

_runtime: CronRuntime | None = None


def set_cron_runtime(runtime: CronRuntime | None) -> None:
    """serve lifespan 启动时注入 cron 运行时。"""
    global _runtime  # noqa: PLW0603
    _runtime = runtime


def _require_runtime() -> CronRuntime:
    if _runtime is None:
        raise RuntimeError("定时任务子系统未启动")
    return _runtime


def _job_to_wire(job: Job, scheduler: Scheduler) -> dict:
    """Job → 前端 wire 字典，附加 APScheduler 的下次触发时间。"""
    data = job.to_dict()
    next_run = scheduler.next_run_time(job.id)
    data["next_run"] = next_run.isoformat() if next_run else None
    return data


async def _job_to_wire_with_runs(
    job: Job, scheduler: Scheduler, service: CronService
) -> dict:
    """列表用的 wire 字典，额外附上近期可跳转的 run。

    run_threads 是前端未读角标的唯一数据源（减去本地已读集合即未读数）——派生而非
    累积，故桌面端离线期间执行的 run 重连后照样算未读。取 MAX_CRON_RUN_THREADS 条：
    超出保留窗口的记录 thread_id 已被 prune 清空，本就不可跳转。

    只有列表带这个字段：单个任务的增删改响应里前端用不到，不必为它读一遍日志。
    """
    data = _job_to_wire(job, scheduler)
    data["run_threads"] = await service.recent_thread_ids(job.id, MAX_CRON_RUN_THREADS)
    return data


async def dispatch_cron(method: str, params: dict) -> dict:
    """执行一个 cron RPC 方法（method 已确认属于 CRON_METHODS）。"""
    rt = _require_runtime()
    service = CronService(rt.scheduler, rt.job_store, rt.run_log)

    if method == "list_cron_jobs":
        jobs = await service.get_all()
        # 每个任务各读一次日志尾部，并发取（任务数可观时省掉逐个 await 的串行等待）
        wire = await asyncio.gather(
            *(_job_to_wire_with_runs(j, rt.scheduler, service) for j in jobs)
        )
        return {"jobs": wire}

    if method == "create_cron_job":
        job = await service.create(
            params.get("name") or "",
            params.get("schedule") or "",
            params.get("prompt") or "",
        )
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "update_cron_job":
        job = await service.update(
            params.get("job_id", ""),
            name=params.get("name"),
            schedule_raw=params.get("schedule"),
            prompt=params.get("prompt"),
        )
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "delete_cron_job":
        job_id = params.get("job_id", "")
        await service.delete(job_id)
        return {"job_id": job_id}

    if method == "toggle_cron_job":
        job = await service.set_enabled(
            params.get("job_id", ""), bool(params.get("enabled"))
        )
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "run_cron_job":
        await service.trigger(params.get("job_id", ""))
        return {"ok": True}

    if method == "stop_cron_run":
        stopped = service.stop(params.get("job_id", ""))
        return {"stopped": stopped}

    # list_cron_runs
    records = await service.recent_runs(
        params.get("job_id", ""), limit=params.get("limit", 20)
    )
    return {"runs": [r.to_dict() for r in records]}

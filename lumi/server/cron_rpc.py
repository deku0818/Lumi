"""Cron RPC：desktop WS 的定时任务管理方法实现。

运行时（CronRuntime）由 serve lifespan 通过 ``set_cron_runtime()`` 注入，
未注入（初始化失败）时所有方法抛 RuntimeError，由 WS 层转为 error 帧。
错误消息（如调度规则解析失败）直接面向前端展示。
"""

from __future__ import annotations

from lumi.agents.cron.models import Job, Schedule
from lumi.agents.cron.runtime import CronRuntime
from lumi.agents.cron.scheduler import Scheduler

CRON_METHODS = frozenset(
    {
        "list_cron_jobs",
        "create_cron_job",
        "update_cron_job",
        "delete_cron_job",
        "toggle_cron_job",
        "run_cron_job",
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


async def _get_job_or_raise(job_id: str) -> Job:
    job = await _require_runtime().job_store.get(job_id)
    if job is None:
        raise ValueError(f"任务 {job_id} 不存在")
    return job


async def dispatch_cron(method: str, params: dict) -> dict:
    """执行一个 cron RPC 方法（method 已确认属于 CRON_METHODS）。"""
    rt = _require_runtime()

    if method == "list_cron_jobs":
        jobs = await rt.job_store.get_all()
        return {"jobs": [_job_to_wire(j, rt.scheduler) for j in jobs]}

    if method == "create_cron_job":
        name = (params.get("name") or "").strip()
        prompt = (params.get("prompt") or "").strip()
        if not name or not prompt:
            raise ValueError("任务名称和提示词不能为空")
        job = Job(
            name=name,
            schedule=Schedule.parse(params.get("schedule") or ""),
            prompt=prompt,
        )
        await rt.job_store.upsert(job)
        rt.scheduler.add_job(job)
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "update_cron_job":
        job = await _get_job_or_raise(params.get("job_id", ""))
        # 字段语义：缺省/None = 不修改；显式传空串 = 报错（与 create 的校验一致）
        name = params.get("name")
        prompt = params.get("prompt")
        if (name is not None and not name.strip()) or (
            prompt is not None and not prompt.strip()
        ):
            raise ValueError("任务名称和提示词不能为空")
        schedule_raw = params.get("schedule")
        if schedule_raw is not None:
            job.schedule = Schedule.parse(schedule_raw)
        if name is not None:
            job.name = name.strip()
        if prompt is not None:
            job.prompt = prompt.strip()
        await rt.job_store.upsert(job)
        if schedule_raw is not None and job.enabled:
            rt.scheduler.add_job(job)
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "delete_cron_job":
        job_id = params.get("job_id", "")
        await _get_job_or_raise(job_id)
        rt.scheduler.remove_job(job_id)
        await rt.job_store.delete(job_id)
        # 级联清理执行日志与历史会话 checkpoint，避免孤儿数据
        await rt.scheduler.purge_job_data(job_id)
        return {"job_id": job_id}

    if method == "toggle_cron_job":
        job = await _get_job_or_raise(params.get("job_id", ""))
        job.enabled = bool(params.get("enabled"))
        await rt.job_store.upsert(job)
        if job.enabled:
            rt.scheduler.add_job(job)
        else:
            rt.scheduler.remove_job(job.id)
        return {"job": _job_to_wire(job, rt.scheduler)}

    if method == "run_cron_job":
        # 先校验存在性：trigger 的 KeyError str() 会带引号，不适合直接回显前端
        job = await _get_job_or_raise(params.get("job_id", ""))
        await rt.scheduler.trigger(job.id)
        return {"ok": True}

    # list_cron_runs
    records = await rt.run_log.get_recent(
        params.get("job_id", ""), limit=params.get("limit", 20)
    )
    return {"runs": [r.to_dict() for r in records]}

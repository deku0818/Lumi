"""CronService：cron 任务的「持久化 + 调度器」操作的唯一来源。

agent 工具（``providers/cron.py``）与 desktop WS（``server/cron_rpc.py``）两个调用方
各自只做「输入校验 + 响应格式化」的薄封装，CRUD/toggle/trigger 的 canonical 行为
（strip、空值校验、JobStore 与 Scheduler 的协同顺序）全部收敛在此。

方法返回 ``Job`` / 数据对象，不含任何展示文本或 wire 包装。
"""

from __future__ import annotations

from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, Schedule
from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.agents.cron.scheduler import Scheduler


class CronService:
    """cron 任务的持久化与调度操作中枢，两个调用方共用。"""

    def __init__(
        self, scheduler: Scheduler, job_store: JobStore, run_log: RunLog
    ) -> None:
        self._scheduler = scheduler
        self._job_store = job_store
        self._run_log = run_log

    async def create(self, name: str, schedule_raw: str, prompt: str) -> Job:
        """创建并注册任务。name/prompt strip 后均须非空。"""
        name = name.strip()
        prompt = prompt.strip()
        if not name or not prompt:
            raise ValueError("任务名称和提示词不能为空")
        job = Job(name=name, schedule=Schedule.parse(schedule_raw), prompt=prompt)
        await self._job_store.upsert(job)
        self._scheduler.add_job(job)
        return job

    async def update(
        self,
        job_id: str,
        *,
        name: str | None = None,
        schedule_raw: str | None = None,
        prompt: str | None = None,
    ) -> Job:
        """修改任务。字段语义：None = 不修改；显式空串 = 报错（与 create 校验一致）。"""
        job = await self.get_or_raise(job_id)
        if (name is not None and not name.strip()) or (
            prompt is not None and not prompt.strip()
        ):
            raise ValueError("任务名称和提示词不能为空")

        schedule_changed = False
        if schedule_raw is not None:
            job.schedule = Schedule.parse(schedule_raw)
            schedule_changed = True
        if name is not None:
            job.name = name.strip()
        if prompt is not None:
            job.prompt = prompt.strip()

        await self._job_store.upsert(job)
        if schedule_changed and job.enabled:
            self._scheduler.add_job(job)
        return job

    async def delete(self, job_id: str) -> Job:
        """删除任务，返回被删除的 Job 供调用方取 name。"""
        job = await self.get_or_raise(job_id)
        await self._scheduler.delete_job(job_id)
        return job

    async def set_enabled(self, job_id: str, enabled: bool) -> Job:
        """启用/暂停任务并同步 APScheduler 注册状态。"""
        job = await self.get_or_raise(job_id)
        job.enabled = enabled
        await self._job_store.upsert(job)
        if enabled:
            self._scheduler.add_job(job)
        else:
            self._scheduler.remove_job(job.id)
        return job

    async def trigger(self, job_id: str) -> Job:
        """立即执行一次任务。先 get_or_raise 保证友好错误（而非 KeyError）。"""
        job = await self.get_or_raise(job_id)
        await self._scheduler.trigger(job.id)
        return job

    async def get_all(self) -> list[Job]:
        """获取所有任务。"""
        return await self._job_store.get_all()

    async def get_or_raise(self, job_id: str) -> Job:
        """按 ID 获取任务，不存在则抛出友好的 ValueError。"""
        job = await self._job_store.get(job_id)
        if job is None:
            raise ValueError(f"任务 {job_id} 不存在")
        return job

    async def recent_runs(self, job_id: str, limit: int) -> list[RunRecord]:
        """获取最近 limit 条执行记录。"""
        return await self._run_log.get_recent(job_id, limit=limit)

    async def recent_thread_ids(self, job_id: str, keep: int) -> list[str]:
        """最近 keep 条执行里可跳转的会话 thread_id（从新到旧）。"""
        return await self._run_log.recent_thread_ids(job_id, keep)

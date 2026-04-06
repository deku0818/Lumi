"""Scheduler：APScheduler 薄封装，管理任务调度和执行。

对 APScheduler ``AsyncIOScheduler`` 的薄封装，负责：
- 从 JobStore 加载任务并注册到 APScheduler
- 添加/移除/暂停/恢复任务
- 管理调度器启停生命周期
- 创建独立 Agent 子会话执行任务，支持超时和结果投递
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, ScheduleType
from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# 退避重试间隔（秒）：第 1、2、3 次重试
BACKOFF_INTERVALS: tuple[int, ...] = (30, 60, 300)
MAX_RETRIES: int = 3


def _is_transient_error(exc: BaseException) -> bool:
    """判断异常是否为瞬态错误，瞬态错误可触发重试。

    瞬态错误包括：
    - asyncio.TimeoutError（网络超时）
    - httpx.HTTPStatusError 且状态码为 429 或 5xx
    - ConnectionError、OSError（网络连接问题）
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # httpx 可能未安装，延迟检查
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code == 429 or code >= 500
    except ImportError:
        pass

    return isinstance(exc, (ConnectionError, OSError))


class Scheduler:
    """APScheduler 薄封装，管理任务调度和执行。

    启动时从 JobStore 加载所有启用的任务并注册到 AsyncIOScheduler，
    提供任务的增删改查和暂停/恢复操作。
    """

    def __init__(
        self,
        job_store: JobStore,
        run_log: RunLog,
        delivery: DeliveryManager,
        execution_timeout: int = 600,
        on_job_status: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._aps = AsyncIOScheduler()
        self._job_store = job_store
        self._run_log = run_log
        self._delivery = delivery
        self._execution_timeout = execution_timeout
        self._running_tasks: set[asyncio.Task[RunRecord]] = set()
        self._running_job_names: list[str] = []
        self._on_job_status = on_job_status
        self._compensate_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """从 JobStore 加载所有启用的任务，注册到 APScheduler 并启动调度器。

        启动后检查是否有错过的任务需要补偿执行。
        """
        jobs = await self._job_store.load()
        for job in jobs:
            if not job.enabled:
                continue
            try:
                self._register_job(job)
            except Exception:
                logger.warning(
                    "注册任务失败，跳过: %s [%s]", job.name, job.id, exc_info=True
                )
        self._aps.start()
        logger.info("Scheduler 已启动，加载了 %d 个任务", len(jobs))

        enabled_jobs = [j for j in jobs if j.enabled]
        if enabled_jobs:
            self._compensate_task = asyncio.create_task(
                self._compensate_missed_runs(enabled_jobs)
            )
            self._compensate_task.add_done_callback(self._on_compensate_done)

    @staticmethod
    def _on_compensate_done(task: asyncio.Task[None]) -> None:
        """补偿任务完成回调，记录未被内部 try-except 捕获的异常。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("补偿任务异常终止: %s", exc, exc_info=exc)

    async def _compensate_missed_runs(self, jobs: list[Job]) -> None:
        """检查并补偿在离线期间错过的任务，有则补执行一次（coalesce）。"""
        now = datetime.now()
        compensated = 0

        for job in jobs:
            try:
                if await self._should_compensate(job, now):
                    logger.info("补偿执行错过的任务: %s [%s]", job.name, job.id)
                    await self._run_job_task(job)
                    compensated += 1
            except Exception:
                logger.warning(
                    "检查错过任务失败: %s [%s] (schedule=%s/%s)",
                    job.name,
                    job.id,
                    job.schedule.type.value,
                    job.schedule.value,
                    exc_info=True,
                )

        if compensated:
            logger.info("补偿执行了 %d 个错过的任务", compensated)

    async def _should_compensate(self, job: Job, now: datetime) -> bool:
        """判断任务是否需要补偿执行。"""
        last_run = await asyncio.to_thread(self._run_log.get_last_run_sync, job.id)

        match job.schedule.type:
            case ScheduleType.AT:
                run_date = datetime.fromisoformat(job.schedule.value)
                if run_date >= now:
                    return False
                return last_run is None or last_run.status != "success"

            case ScheduleType.INTERVAL:
                from lumi.agents.cron.models import parse_interval_to_seconds

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

    async def stop(self, grace_period: int = 30) -> None:
        """优雅停止调度器，等待执行中的任务完成。

        先关闭 APScheduler（不再触发新任务），然后等待所有正在执行的
        任务完成，最多等待 ``grace_period`` 秒。超时后取消剩余任务。

        Args:
            grace_period: 等待执行中任务完成的最大秒数，默认 30。
        """
        if not self._aps.running:
            return
        self._aps.shutdown(wait=False)

        if self._compensate_task and not self._compensate_task.done():
            self._compensate_task.cancel()

        if self._running_tasks:
            logger.info(
                "等待 %d 个执行中的任务完成（最多 %d 秒）",
                len(self._running_tasks),
                grace_period,
            )
            _, pending = await asyncio.wait(self._running_tasks, timeout=grace_period)
            for task in pending:
                task.cancel()
                logger.warning("任务执行超时，已取消: %s", task.get_name())

        self._running_tasks.clear()
        logger.info("Scheduler 已停止")

    def _register_job(self, job: Job) -> None:
        """将 Job 注册到 APScheduler。

        使用 ``job.schedule.to_trigger()`` 创建触发器，
        以 ``job.id`` 作为 APScheduler 任务 ID，已存在则替换。
        APScheduler 回调指向 ``_run_job_task``，将执行包装为 asyncio.Task。

        Args:
            job: 要注册的任务。
        """
        trigger = job.schedule.to_trigger()
        self._aps.add_job(
            self._run_job_task,
            trigger=trigger,
            args=[job],
            id=job.id,
            replace_existing=True,
        )

    async def get_all_jobs(self) -> list[Job]:
        """获取所有持久化任务。

        Returns:
            所有任务列表。
        """
        return await self._job_store.get_all()

    async def get_job(self, job_id: str) -> Job | None:
        """按 ID 获取单个任务。

        Args:
            job_id: 任务 ID。

        Returns:
            任务对象，不存在时返回 None。
        """
        return await self._job_store.get(job_id)

    async def delete_job(self, job_id: str) -> None:
        """从 APScheduler 和 JobStore 中删除任务。

        Args:
            job_id: 要删除的任务 ID。
        """
        self.remove_job(job_id)
        await self._job_store.delete(job_id)

    def add_job(self, job: Job) -> None:
        """添加新任务到 APScheduler。

        Args:
            job: 要添加的任务。
        """
        self._register_job(job)

    def remove_job(self, job_id: str) -> None:
        """从 APScheduler 移除任务。

        Args:
            job_id: 要移除的任务 ID。
        """
        try:
            self._aps.remove_job(job_id)
        except JobLookupError:
            pass  # job may not be registered (disabled at startup)

    def pause_job(self, job_id: str) -> None:
        """暂停 APScheduler 中的任务。

        Args:
            job_id: 要暂停的任务 ID。
        """
        self._aps.pause_job(job_id)

    def resume_job(self, job_id: str) -> None:
        """恢复 APScheduler 中已暂停的任务。

        Args:
            job_id: 要恢复的任务 ID。
        """
        self._aps.resume_job(job_id)

    async def trigger(self, job_id: str) -> None:
        """立即执行一次指定任务，不影响 APScheduler 中的正常调度。

        从 JobStore 加载任务并通过 ``_run_job_task`` 创建独立执行，
        不修改 APScheduler 中该任务的 trigger 或下次触发时间。

        Args:
            job_id: 要立即执行的任务 ID。

        Raises:
            KeyError: 任务 ID 不存在。
        """
        job = await self._job_store.get(job_id)
        if job is None:
            raise KeyError(f"任务 {job_id} 不存在")
        await self._run_job_task(job)

    async def _run_job_task(self, job: Job) -> None:
        """将 _execute_job 包装为 asyncio.Task 并管理 _running_tasks 集合。

        APScheduler 回调入口。创建 asyncio.Task 执行任务，
        任务完成后自动从 ``_running_tasks`` 中移除。

        Args:
            job: 要执行的任务。
        """
        task = asyncio.create_task(self._execute_job(job), name=f"cron-job-{job.id}")
        self._running_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[RunRecord]) -> None:
        self._running_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()):
            logger.error("定时任务意外失败: %s", exc, exc_info=exc)

    # ------------------------------------------------------------------
    # Job execution: split into invoke / retry / deliver sub-functions
    # ------------------------------------------------------------------

    async def _execute_job(self, job: Job) -> RunRecord:
        """执行单个任务：Agent 调用、重试判定、结果投递与日志记录。"""
        started_at = datetime.now()

        self._running_job_names.append(job.name)
        self._notify_job_status()

        try:
            output, status, error, caught_exc = await self._invoke_agent(job)
            await self._handle_retry(job, caught_exc)

            finished_at = datetime.now()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

            record = RunRecord(
                job_id=job.id,
                job_name=job.name,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                duration_ms=duration_ms,
                output_summary=output[:500],
                error=error,
            )
            await self._deliver_and_log(job, record, output)
            return record
        finally:
            try:
                self._running_job_names.remove(job.name)
            except ValueError:
                logger.warning("任务 %s [%s] 不在 running 列表中", job.name, job.id)
            self._notify_job_status()

    async def _invoke_agent(self, job: Job) -> tuple[str, str, str, Exception | None]:
        """创建 Agent 子会话并执行任务 prompt，返回 (output, status, error, exception)。"""
        from lumi.agents.core.graph import create_agent

        try:
            agent, context = await create_agent(checkpoint=None)
            inputs = {
                "messages": [HumanMessage(content=job.prompt)],
                "tool_mode": "privileged",
            }
            config = RunnableConfig(
                recursion_limit=get_config().config.agents.recursion_limit,
            )
            response = await asyncio.wait_for(
                agent.graph.ainvoke(inputs, config=config, context=context),
                timeout=self._execution_timeout,
            )
            output = self._extract_output(response)
            return output, "success", "", None

        except asyncio.TimeoutError as exc:
            logger.warning("任务执行超时: %s [%s]", job.name, job.id)
            return "", "timeout", f"任务执行超时（{self._execution_timeout}s）", exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("任务执行失败: %s [%s]", job.name, job.id)
            return "", "failed", f"{type(exc).__name__}: {exc}", exc

    @staticmethod
    def _extract_output(response: dict) -> str:
        """从 Agent 响应中提取纯文本输出。"""
        messages = response.get("messages", [])
        if not messages:
            raise ValueError("Agent 响应中无消息")
        last_msg = messages[-1]
        raw_content = (
            last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        )
        if isinstance(raw_content, list):
            return "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw_content
            ).strip()
        return str(raw_content)

    async def _handle_retry(self, job: Job, caught_exc: Exception | None) -> None:
        """根据执行结果决定是否安排退避重试或重置错误计数。"""
        if caught_exc is not None and _is_transient_error(caught_exc):
            if job.consecutive_errors < MAX_RETRIES:
                job.consecutive_errors += 1
                await self._persist_consecutive_errors(job)
                self._schedule_retry(job)
            else:
                logger.error(
                    "任务重试次数耗尽（%d/%d），记录最终失败: %s [%s]",
                    job.consecutive_errors,
                    MAX_RETRIES,
                    job.name,
                    job.id,
                )
        elif caught_exc is None and job.consecutive_errors > 0:
            job.consecutive_errors = 0
            await self._persist_consecutive_errors(job)

    async def _deliver_and_log(
        self,
        job: Job,
        record: RunRecord,
        output: str,
    ) -> None:
        """记录执行日志、广播结果、清理一次性任务。"""
        try:
            await self._run_log.append(record)
        except Exception:
            logger.warning("记录执行日志失败: %s [%s]", job.name, job.id, exc_info=True)

        broadcast_text = (
            output
            if record.status == "success"
            else f"[{record.status}] {record.error}"
        )
        try:
            await self._delivery.broadcast(
                job.name,
                broadcast_text,
                started_at=record.started_at,
                duration_ms=record.duration_ms,
            )
        except Exception:
            logger.warning("广播结果失败: %s [%s]", job.name, job.id, exc_info=True)

        if job.schedule.type == ScheduleType.AT:
            try:
                await self._job_store.delete(job.id)
                logger.info("一次性任务已完成并删除: %s [%s]", job.name, job.id)
            except Exception:
                logger.warning(
                    "删除一次性任务失败: %s [%s]", job.name, job.id, exc_info=True
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _notify_job_status(self) -> None:
        """将当前正在执行的任务名列表通知给 TUI。"""
        if not self._on_job_status:
            return
        try:
            self._on_job_status(list(self._running_job_names))
        except Exception:
            logger.error("通知 TUI 任务状态失败", exc_info=True)

    def _schedule_retry(self, job: Job) -> None:
        """通过 APScheduler DateTrigger 安排退避重试。"""
        idx = min(job.consecutive_errors - 1, len(BACKOFF_INTERVALS) - 1)
        delay = BACKOFF_INTERVALS[idx]
        run_at = datetime.now() + timedelta(seconds=delay)
        retry_id = f"{job.id}-retry-{job.consecutive_errors}"

        self._aps.add_job(
            self._run_job_task,
            trigger=DateTrigger(run_date=run_at),
            args=[job],
            id=retry_id,
            replace_existing=True,
        )
        logger.info(
            "已安排重试 %d/%d，%d 秒后执行: %s [%s]",
            job.consecutive_errors,
            MAX_RETRIES,
            delay,
            job.name,
            job.id,
        )

    async def _persist_consecutive_errors(self, job: Job) -> None:
        """将 Job 的 consecutive_errors 持久化到 JobStore。"""
        try:
            await self._job_store.upsert(job)
        except Exception:
            logger.warning(
                "更新 consecutive_errors 失败: %s [%s]",
                job.name,
                job.id,
                exc_info=True,
            )

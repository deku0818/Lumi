"""Scheduler：APScheduler 薄封装，管理任务调度和执行。

对 APScheduler ``AsyncIOScheduler`` 的薄封装，负责：
- 从 JobStore 加载任务并注册到 APScheduler
- 添加/移除/暂停/恢复任务
- 管理调度器启停生命周期
- 创建独立 Agent 子会话执行任务，支持超时和结果投递
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from langchain_core.messages import HumanMessage

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, ScheduleType
from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.utils.logger import logger

# 退避重试间隔（秒）：第 1、2、3 次重试
BACKOFF_INTERVALS: tuple[int, ...] = (30, 60, 300)
MAX_RETRIES = 3


def _is_transient_error(exc: BaseException) -> bool:
    """判断异常是否为瞬态错误，瞬态错误可触发重试。

    瞬态错误包括：
    - asyncio.TimeoutError（网络超时）
    - httpx.HTTPStatusError 且状态码为 429 或 5xx
    - ConnectionError、OSError（网络连接问题）

    Args:
        exc: 捕获到的异常。

    Returns:
        True 表示瞬态错误，应重试；False 表示永久错误，不重试。
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

    if isinstance(exc, (ConnectionError, OSError)):
        return True

    return False


class Scheduler:
    """APScheduler 薄封装，管理任务调度和执行。

    启动时从 JobStore 加载所有启用的任务并注册到 AsyncIOScheduler，
    提供任务的增删改查和暂停/恢复操作。

    Args:
        job_store: 任务持久化存储。
        run_log: 执行日志管理。
        delivery: 结果投递管理器。
        execution_timeout: 单次任务执行超时（秒），默认 600（10 分钟）。
    """

    def __init__(
        self,
        job_store: JobStore,
        run_log: RunLog,
        delivery: DeliveryManager,
        execution_timeout: int = 600,
    ) -> None:
        self._aps = AsyncIOScheduler()
        self._job_store = job_store
        self._run_log = run_log
        self._delivery = delivery
        self._execution_timeout = execution_timeout
        self._running_tasks: set[asyncio.Task[RunRecord]] = set()

    async def start(self) -> None:
        """从 JobStore 加载所有启用的任务，注册到 APScheduler 并启动调度器。"""
        jobs = await self._job_store.load()
        for job in jobs:
            if job.enabled:
                try:
                    self._register_job(job)
                except Exception:
                    logger.warning(
                        "注册任务失败，跳过: %s [%s]", job.name, job.id, exc_info=True
                    )
        self._aps.start()
        logger.info("Scheduler 已启动，加载了 %d 个任务", len(jobs))

    async def stop(self, grace_period: int = 30) -> None:
        """优雅停止调度器，等待执行中的任务完成。

        先关闭 APScheduler（不再触发新任务），然后等待所有正在执行的
        任务完成，最多等待 ``grace_period`` 秒。超时后取消剩余任务。

        Args:
            grace_period: 等待执行中任务完成的最大秒数，默认 30。
        """
        self._aps.shutdown(wait=False)

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

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._running_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()):
            logger.error("定时任务意外失败: %s", exc, exc_info=exc)

    async def _execute_job(self, job: Job) -> RunRecord:
        """执行单个任务：创建独立 Agent 子会话，处理超时、重试，广播结果并记录日志。

        流程：
        1. 调用 ``create_agent(checkpoint=None)`` 创建无状态子 Agent
        2. 使用 ``asyncio.wait_for`` 限制执行时间（默认 10 分钟）
        3. 失败时判断是否为瞬态错误，若是且未超过重试上限则安排退避重试
        4. 成功执行后重置 ``consecutive_errors`` 为 0
        5. 通过 ``DeliveryManager.broadcast()`` 广播结果
        6. 记录 ``RunRecord`` 到 ``RunLog``
        7. 一次性任务（at 类型）执行后从 JobStore 删除

        Args:
            job: 要执行的任务。

        Returns:
            执行记录。
        """
        from lumi.agents.core.graph import create_agent

        started_at = datetime.now()
        status: str = "success"
        output: str = ""
        error: str = ""
        caught_exc: BaseException | None = None

        try:
            agent, context = await create_agent(checkpoint=None)
            inputs = {
                "messages": [HumanMessage(content=job.prompt)],
                # 定时任务无人在场审批，使用 privileged 模式跳过 interrupt
                "tool_mode": "privileged",
            }
            response = await asyncio.wait_for(
                agent.graph.ainvoke(inputs, context=context),
                timeout=self._execution_timeout,
            )
            # 从 response 中提取输出文本
            messages = response.get("messages", [])
            if not messages:
                raise ValueError("Agent 响应中无消息")
            last_msg = messages[-1]
            output = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        except asyncio.TimeoutError as exc:
            status = "timeout"
            error = f"任务执行超时（{self._execution_timeout}s）"
            caught_exc = exc
            logger.warning("任务执行超时: %s [%s]", job.name, job.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            caught_exc = exc
            logger.exception("任务执行失败: %s [%s]", job.name, job.id)

        # --- 重试逻辑 ---
        if caught_exc is not None and _is_transient_error(caught_exc):
            if job.consecutive_errors < MAX_RETRIES:
                job.consecutive_errors += 1
                await self._update_consecutive_errors(job)
                self._schedule_retry(job)
            else:
                logger.error(
                    "任务重试次数耗尽（%d/%d），记录最终失败: %s [%s]",
                    job.consecutive_errors,
                    MAX_RETRIES,
                    job.name,
                    job.id,
                )
        elif caught_exc is None:
            # 成功执行，重置连续错误计数
            if job.consecutive_errors > 0:
                job.consecutive_errors = 0
                await self._update_consecutive_errors(job)

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

        # 记录执行日志
        try:
            await self._run_log.append(record)
        except Exception:
            logger.warning("记录执行日志失败: %s [%s]", job.name, job.id, exc_info=True)

        # 广播结果（成功时广播输出，失败/超时时广播错误信息）
        broadcast_text = output if status == "success" else f"[{status}] {error}"
        try:
            await self._delivery.broadcast(
                job.name,
                broadcast_text,
                started_at=started_at,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.warning("广播结果失败: %s [%s]", job.name, job.id, exc_info=True)

        # 一次性任务（at 类型）执行后从 JobStore 删除
        if job.schedule.type == ScheduleType.AT:
            try:
                await self._job_store.delete(job.id)
                logger.info("一次性任务已完成并删除: %s [%s]", job.name, job.id)
            except Exception:
                logger.warning(
                    "删除一次性任务失败: %s [%s]", job.name, job.id, exc_info=True
                )

        return record

    def _schedule_retry(self, job: Job) -> None:
        """通过 APScheduler DateTrigger 安排退避重试。

        根据当前 ``consecutive_errors`` 选择退避间隔，使用 DateTrigger
        在指定时间后触发一次重试执行。

        Args:
            job: 需要重试的任务。
        """
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

    async def _update_consecutive_errors(self, job: Job) -> None:
        """将 Job 的 consecutive_errors 更新到 JobStore。

        Args:
            job: 已更新 consecutive_errors 的任务。
        """
        try:
            await self._job_store.upsert(job)
        except Exception:
            logger.warning(
                "更新 consecutive_errors 失败: %s [%s]",
                job.name,
                job.id,
                exc_info=True,
            )

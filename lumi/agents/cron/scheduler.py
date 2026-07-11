"""Scheduler：APScheduler 薄封装，管理任务调度和执行。

对 APScheduler ``AsyncIOScheduler`` 的薄封装，负责：
- 从 JobStore 加载任务并注册到 APScheduler
- 添加/移除/暂停/恢复任务
- 管理调度器启停生命周期
- 创建独立 Agent 子会话执行任务，支持超时和结果投递
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from lumi.agents.core.meta_message import synthetic_human_message
from lumi.agents.cron.compensation import should_compensate
from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_runner import extract_output
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, ScheduleType
from lumi.agents.cron.retry import backoff_delay, is_transient_error
from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.agents.runtime.bg_tasks import current_thread_id
from lumi.agents.runtime.checkpoint import delete_thread_checkpoint
from lumi.utils.constants import (
    MAX_CRON_RETRIES,
    MAX_CRON_RUN_THREADS,
)
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import CRON_THREAD_PREFIX, generate_thread_id

# 向后兼容：历史上 ``_is_transient_error`` 定义在本模块，外部（含测试）经此路径导入。
_is_transient_error = is_transient_error


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
        lock_path: Path | None = None,
    ) -> None:
        self._aps = AsyncIOScheduler()
        self._job_store = job_store
        self._run_log = run_log
        self._delivery = delivery
        self._execution_timeout = execution_timeout
        # 跨进程调度互斥锁文件（None = 不互斥，测试用）
        self._lock_path = lock_path
        self._lock_file: IO[str] | None = None
        self._running_tasks: set[asyncio.Task[RunRecord]] = set()
        self._running_job_names: list[str] = []
        self._on_job_status = on_job_status
        self._compensate_task: asyncio.Task[None] | None = None
        # 常驻 checkpointer：所有 run 共用一条连接，每次执行独立 cron- thread，
        # 使执行过程像普通会话一样可回看、可续聊。初始化失败时退化为无会话模式。
        self._checkpointer: BaseCheckpointSaver | None = None

    async def start(self) -> None:
        """从 JobStore 加载所有启用的任务，注册到 APScheduler 并启动调度器。

        启动后检查是否有错过的任务需要补偿执行。
        """
        try:
            from lumi.agents.core.graph import create_checkpointer

            self._checkpointer = await create_checkpointer(
                get_config().config.agents.checkpoint
            )
        except Exception:
            logger.warning(
                "cron checkpointer 初始化失败，执行记录将不带会话", exc_info=True
            )

        # 同一 workspace 的 jobs.json 可能同时被 TUI 与 lumi serve 加载，
        # 不互斥的话每个任务会在每个进程各执行一次
        if not self._try_acquire_lock():
            logger.info(
                "另一进程正在调度本工作区的定时任务，本进程跳过调度（仍可管理任务）"
            )
            return

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
        return should_compensate(job, now, last_run)

    async def stop(self, grace_period: int = 30) -> None:
        """优雅停止调度器，等待执行中的任务完成。

        先关闭 APScheduler（不再触发新任务），然后等待所有正在执行的
        任务完成，最多等待 ``grace_period`` 秒。超时后取消剩余任务。

        Args:
            grace_period: 等待执行中任务完成的最大秒数，默认 30。
        """
        if self._aps.running:
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

        if self._checkpointer is not None:
            from lumi.agents.core.graph import close_checkpointer

            await close_checkpointer(self._checkpointer)
            self._checkpointer = None

        self._release_lock()
        logger.info("Scheduler 已停止")

    def _try_acquire_lock(self) -> bool:
        """跨进程调度互斥：同一 workspace 仅一个进程调度，后启动者跳过。"""
        if self._lock_path is None:
            return True
        import fcntl

        # "a+" 不截断：抢锁失败时不抹掉持有进程已写入的 PID
        f = open(self._lock_path, "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        self._lock_file = f
        return True

    def _release_lock(self) -> None:
        if self._lock_file is not None:
            self._lock_file.close()  # close 即释放 flock
            self._lock_file = None

    def _register_job(self, job: Job) -> None:
        """将 Job 注册到 APScheduler。

        使用 ``job.schedule.to_trigger()`` 创建触发器，
        以 ``job.id`` 作为 APScheduler 任务 ID，已存在则替换。
        回调指向 ``_fire_job``（只携带 job.id）：触发时从 JobStore 重读最新
        定义——若携带 Job 对象，注册后对 prompt/name 的更新将永远不生效。

        Args:
            job: 要注册的任务。
        """
        trigger = job.schedule.to_trigger()
        self._aps.add_job(
            self._fire_job,
            trigger=trigger,
            args=[job.id],
            id=job.id,
            replace_existing=True,
        )

    async def _fire_job(self, job_id: str) -> None:
        """APScheduler 触发入口：按 id 重读最新任务定义后执行。"""
        job = await self._job_store.get(job_id)
        if job is None or not job.enabled:
            return
        await self._run_job_task(job)

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
        """删除任务并级联清理执行日志与历史会话 checkpoint（避免孤儿数据）。

        Args:
            job_id: 要删除的任务 ID。
        """
        self.remove_job(job_id)
        await self._job_store.delete(job_id)
        await self.purge_job_data(job_id)

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

    def next_run_time(self, job_id: str) -> datetime | None:
        """查询任务在 APScheduler 中的下次触发时间。

        Args:
            job_id: 任务 ID。

        Returns:
            下次触发时间；任务未注册（如已暂停）时返回 None。
        """
        aps_job = self._aps.get_job(job_id)
        return getattr(aps_job, "next_run_time", None) if aps_job else None

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
            output, status, error, caught_exc, thread_id = await self._invoke_agent(job)
            retry_scheduled = await self._handle_retry(job, caught_exc)

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
                thread_id=thread_id,
            )
            await self._deliver_and_log(job, record, output, retry_scheduled)
            return record
        finally:
            try:
                self._running_job_names.remove(job.name)
            except ValueError:
                logger.warning("任务 %s [%s] 不在 running 列表中", job.name, job.id)
            self._notify_job_status()

    async def _invoke_agent(
        self, job: Job
    ) -> tuple[str, str, str, Exception | None, str]:
        """创建 Agent 子会话并执行任务 prompt。

        Returns:
            (output, status, error, exception, thread_id) 元组。checkpointer 可用时
            每次执行落在独立的 cron- thread 中（像普通会话一样可回看、可续聊），
            超时/失败的执行也保留中断前的现场。
        """
        # 延迟 import：cron 经 bootstrap→cron.runtime→scheduler 在 tools/permissions 完成
        # 初始化前就被加载，模块顶层引入 core.graph / core.hooks / permissions.workspace
        # 都会触发 permissions/__init__→engine→tools→cron 的循环导入，故调用时再引入。
        from lumi.agents.core.graph import create_agent
        from lumi.agents.core.hooks import build_config_hooks, set_run_config_hooks
        from lumi.agents.permissions.workspace import set_run_authorized_source_for

        thread_id = generate_thread_id(CRON_THREAD_PREFIX) if self._checkpointer else ""
        # 执行中产生的后台任务归属本次 run 的 thread，通知不会被无关会话认领
        current_thread_id.set(thread_id)
        try:
            # 单发执行无下一轮自愈：冷池需等 MCP 工具就位（交互路径才走非阻塞+轮首刷新）。
            # 延迟导入：tools 包经 providers/cron.py 反向依赖本模块，模块级导入成环
            from lumi.agents.tools.providers.mcp import await_pool_ready

            await await_pool_ready(None)
            agent, context = await create_agent(checkpointer=self._checkpointer)
            # cron 直接 ainvoke（不走 bridge._stream），需自行注入本 run 的授权目录来源
            # 与项目 config hooks，否则 filesystem/bash 工具落回被并发会话清洗的进程全局、
            # hooks 也读不到本 cron 项目。降级（无引擎）兜底与 bridge 共用同一 helper。
            eng = context.permission_engine
            set_run_authorized_source_for(eng)
            proj = eng.project_dir if eng is not None else Path.cwd().resolve()
            set_run_config_hooks(build_config_hooks(proj))
            # cron 无交互审批通道，固定 privileged（tool_mode 是 context 属性）
            context.tool_mode = "privileged"
            inputs = {
                # 合成消息（items: []）：任务 prompt 是机器注入的指令而非用户发言，
                # run 视图不显示；prompt 本体在任务详情页可见
                "messages": [synthetic_human_message(job.prompt)],
            }
            config = RunnableConfig(
                recursion_limit=get_config().config.agents.recursion_limit,
            )
            if thread_id:
                config["configurable"] = {"thread_id": thread_id}
            response = await asyncio.wait_for(
                agent.graph.ainvoke(inputs, config=config, context=context),
                timeout=self._execution_timeout,
            )
            output = extract_output(response)
            return output, "success", "", None, thread_id

        except TimeoutError as exc:
            logger.warning("任务执行超时: %s [%s]", job.name, job.id)
            return (
                "",
                "timeout",
                f"任务执行超时（{self._execution_timeout}s）",
                exc,
                thread_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("任务执行失败: %s [%s]", job.name, job.id)
            return "", "failed", f"{type(exc).__name__}: {exc}", exc, thread_id

    async def _handle_retry(self, job: Job, caught_exc: Exception | None) -> bool:
        """根据执行结果决定是否安排退避重试或重置错误计数。

        返回是否已安排重试——调用方据此决定一次性(AT)任务是否可删除：
        已安排重试时若立即删除，重试触发的 _fire_job 会读到 None 而静默丢失。
        """
        if caught_exc is not None and is_transient_error(caught_exc):
            if job.consecutive_errors < MAX_CRON_RETRIES:
                job.consecutive_errors += 1
                await self._persist_consecutive_errors(job)
                self._schedule_retry(job)
                return True
            logger.error(
                "任务重试次数耗尽（%d/%d），记录最终失败: %s [%s]",
                job.consecutive_errors,
                MAX_CRON_RETRIES,
                job.name,
                job.id,
            )
        elif caught_exc is None and job.consecutive_errors > 0:
            job.consecutive_errors = 0
            await self._persist_consecutive_errors(job)
        return False

    async def _deliver_and_log(
        self,
        job: Job,
        record: RunRecord,
        output: str,
        retry_scheduled: bool = False,
    ) -> None:
        """记录执行日志、广播结果、应用会话保留策略、清理一次性任务。"""
        try:
            await self._run_log.append(record)
        except Exception:
            logger.warning("记录执行日志失败: %s [%s]", job.name, job.id, exc_info=True)

        # 会话保留策略：只保留最近 N 次执行的 checkpoint，超出部分清掉
        if self._checkpointer is not None:
            try:
                pruned = await self._run_log.prune_thread_ids(
                    job.id, keep=MAX_CRON_RUN_THREADS
                )
                await asyncio.gather(*(self._delete_thread(t) for t in pruned))
            except Exception:
                logger.warning(
                    "清理历史执行会话失败: %s [%s]", job.name, job.id, exc_info=True
                )

        broadcast_text = (
            output
            if record.status == "success"
            else f"[{record.status}] {record.error}"
        )
        try:
            await self._delivery.broadcast(record, broadcast_text)
        except Exception:
            logger.warning("广播结果失败: %s [%s]", job.name, job.id, exc_info=True)

        # 已安排重试时保留 AT 任务，否则重试触发的 _fire_job 会读到 None 而丢失
        if job.schedule.type == ScheduleType.AT and not retry_scheduled:
            try:
                await self._job_store.delete(job.id)
                logger.info("一次性任务已完成并删除: %s [%s]", job.name, job.id)
            except Exception:
                logger.warning(
                    "删除一次性任务失败: %s [%s]", job.name, job.id, exc_info=True
                )

    async def purge_job_data(self, job_id: str) -> None:
        """级联清理任务的执行日志与全部会话 checkpoint（删除任务时调用）。

        Args:
            job_id: 要清理的任务 ID。
        """
        records = await self._run_log.get_all(job_id)
        await asyncio.gather(
            *(self._delete_thread(r.thread_id) for r in records if r.thread_id)
        )
        await self._run_log.delete_log(job_id)

    async def _delete_thread(self, thread_id: str) -> None:
        """删除单个会话线程的 checkpoint（LangGraph + 文件级），失败仅告警不中断。

        cron 线程在 desktop 中续聊会产生文件级 filediff checkpoint，
        与 AgentBridge.delete_thread 对齐，一并清理避免孤儿目录。
        """
        if self._checkpointer is None or not hasattr(
            self._checkpointer, "adelete_thread"
        ):
            return
        try:
            await self._checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.warning("删除会话 checkpoint 失败: %s", thread_id, exc_info=True)
        try:
            await asyncio.to_thread(delete_thread_checkpoint, thread_id)
        except Exception:
            logger.warning("删除文件级 checkpoint 失败: %s", thread_id, exc_info=True)

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
        delay = backoff_delay(job.consecutive_errors)
        run_at = datetime.now() + timedelta(seconds=delay)
        retry_id = f"{job.id}-retry-{job.consecutive_errors}"

        self._aps.add_job(
            self._fire_job,
            trigger=DateTrigger(run_date=run_at),
            args=[job.id],
            id=retry_id,
            replace_existing=True,
        )
        logger.info(
            "已安排重试 %d/%d，%d 秒后执行: %s [%s]",
            job.consecutive_errors,
            MAX_CRON_RETRIES,
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

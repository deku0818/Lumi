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
from collections.abc import Awaitable, Callable
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
        execution_timeout: int = 6000,
        on_job_status: Callable[[list[dict]], None] | None = None,
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
        # job_id → 执行中的 task：cancel_job / 并发去重按 job_id 引用取消（不靠 task 名匹配）
        self._running_tasks: dict[str, asyncio.Task[RunRecord]] = {}
        # 运行中的 run：job_id → (thread_id, started_at)。运行态广播据此带上 thread_id，
        # 前端在执行记录顶部显示可点进观测的活条目。
        self._active_runs: dict[str, tuple[str, datetime]] = {}
        # 用户主动中断的 job id：cancel_job 置位，_invoke_agent 的取消处理据此把本次
        # 运行记为 "stopped"（而非关机 grace 期的取消——那种照常向上抛，不落 record）。
        self._user_stopped_jobs: set[str] = set()
        # 注入的流式 runner（gateway 用 AgentBridge 跑并 publish 直播事件）。未注入
        # （TUI / 测试）时 fallback 到 create_agent + ainvoke，不直播。见 set_stream_runner。
        self._stream_runner: Callable[[str, str], Awaitable[str]] | None = None
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
            _, pending = await asyncio.wait(
                self._running_tasks.values(), timeout=grace_period
            )
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

    def set_stream_runner(
        self, runner: Callable[[str, str], Awaitable[str]] | None
    ) -> None:
        """注入流式 runner：``async runner(prompt, thread_id) -> output``。

        gateway 用 AgentBridge 跑 job、逐事件 publish 到该 thread 的观测者，返回终态
        output。未注入时 fallback 到 create_agent + ainvoke（不直播，TUI / 测试用）。
        """
        self._stream_runner = runner

    def cancel_job(self, job_id: str) -> bool:
        """中断正在执行的任务：按 job_id 取到 task 并 cancel。

        置 ``_user_stopped_jobs`` 标记，使 ``_invoke_agent`` 把本次运行记为 "stopped"
        （区别于关机 grace 期的取消）。返回是否确有一个运行中的 task 被取消。
        """
        task = self._running_tasks.get(job_id)
        if task is None or task.done():
            return False
        self._user_stopped_jobs.add(job_id)
        task.cancel()
        logger.info("用户中断定时任务执行: [%s]", job_id)
        return True

    async def _run_job_task(self, job: Job) -> None:
        """将 _execute_job 包装为 asyncio.Task 并登记进 ``_running_tasks``（按 job_id）。

        APScheduler 回调入口。同 job 不并发：APScheduler 调度侧 max_instances=1 已挡定时
        重叠，但 run_cron_job 手动触发绕过它——已有一次在跑就跳过（_active_runs / cancel_job
        均按 job_id 单值建模，并发会串号）。
        """
        running = self._running_tasks.get(job.id)
        if running is not None and not running.done():
            logger.info("任务 %s [%s] 已在执行中，跳过本次触发", job.name, job.id)
            return
        task = asyncio.create_task(self._execute_job(job), name=f"cron-job-{job.id}")
        self._running_tasks[job.id] = task
        task.add_done_callback(lambda t: self._on_task_done(job.id, t))

    def _on_task_done(self, job_id: str, task: asyncio.Task[RunRecord]) -> None:
        # 只在登记的仍是本 task 时移除：同 job 紧接着重跑会覆盖该 key，别把新 task 抹掉。
        if self._running_tasks.get(job_id) is task:
            del self._running_tasks[job_id]
        if not task.cancelled() and (exc := task.exception()):
            logger.error("定时任务意外失败: %s", exc, exc_info=exc)

    # ------------------------------------------------------------------
    # Job execution: split into invoke / retry / deliver sub-functions
    # ------------------------------------------------------------------

    async def _execute_job(self, job: Job) -> RunRecord:
        """执行单个任务：Agent 调用、重试判定、结果投递与日志记录。"""
        started_at = datetime.now()
        # thread_id 在起始生成（不再由 _invoke_agent 内部生成）：使运行态广播能带上
        # thread_id，前端在执行记录顶部显示可点进观测的活条目。注入 runner 时 bridge
        # 自带 checkpointer，thread 恒有；仅无 runner 且无常驻 checkpointer 时才空。
        thread_id = (
            generate_thread_id(CRON_THREAD_PREFIX)
            if (self._stream_runner is not None or self._checkpointer)
            else ""
        )
        self._active_runs[job.id] = (thread_id, started_at)
        self._notify_job_status()

        try:
            output, status, error, caught_exc = await self._invoke_agent(job, thread_id)
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
            self._active_runs.pop(job.id, None)
            self._notify_job_status()

    async def _invoke_agent(
        self, job: Job, thread_id: str
    ) -> tuple[str, str, str, Exception | None]:
        """执行任务 prompt，返回 (output, status, error, exception)。

        注入了流式 runner 则走 bridge 直播；否则 fallback 到 create_agent + ainvoke。
        统一包超时 / 取消 / 异常判定；cron- thread 里的现场经 checkpoint 保留、可续聊。
        """
        # 执行中产生的后台任务归属本次 run 的 thread，通知不会被无关会话认领
        current_thread_id.set(thread_id)
        try:
            output = await asyncio.wait_for(
                self._run_agent(job, thread_id), timeout=self._execution_timeout
            )
            return output, "success", "", None
        except TimeoutError as exc:
            logger.warning("任务执行超时: %s [%s]", job.name, job.id)
            return "", "timeout", f"任务执行超时（{self._execution_timeout}s）", exc
        except asyncio.CancelledError:
            # 用户主动中断：吞掉取消、记为 stopped（wait_for 已把内层 graph 掐断，现场经
            # checkpoint 保留、续聊自愈）。关机 grace 期的取消未置标记 → 照常上抛。
            if job.id in self._user_stopped_jobs:
                self._user_stopped_jobs.discard(job.id)
                # 吸收本次取消：uncancel 复位取消计数，使 _execute_job 后续出 record /
                # 投递的 await 不被 asyncio 当作仍在取消而打断（Python 3.11+ 语义）。
                if (task := asyncio.current_task()) is not None:
                    task.uncancel()
                return "", "stopped", "用户中断执行", None
            raise
        except Exception as exc:
            logger.exception("任务执行失败: %s [%s]", job.name, job.id)
            return "", "failed", f"{type(exc).__name__}: {exc}", exc

    async def _run_agent(self, job: Job, thread_id: str) -> str:
        """跑一次 job：优先注入的流式 runner（直播），否则 fallback ainvoke（不直播）。"""
        if self._stream_runner is not None:
            return await self._stream_runner(job.prompt, thread_id)

        # 延迟 import：cron 经 bootstrap→cron.runtime→scheduler 在 tools/permissions 完成
        # 初始化前就被加载，模块顶层引入会触发循环导入，故调用时再引入。
        from lumi.agents.core.graph import create_agent
        from lumi.agents.core.hooks import build_config_hooks, set_run_config_hooks
        from lumi.agents.permissions.workspace import set_run_authorized_source_for

        agent, context = await create_agent(checkpointer=self._checkpointer)
        # 自行注入本 run 的授权目录来源与项目 config hooks（bridge runner 内部自带，
        # 此 fallback 路径需手动）；降级兜底与 bridge 共用同一 helper。
        eng = context.permission_engine
        set_run_authorized_source_for(eng)
        proj = eng.project_dir if eng is not None else Path.cwd().resolve()
        set_run_config_hooks(build_config_hooks(proj))
        context.tool_mode = "privileged"  # cron 无交互审批通道，固定 privileged
        inputs = {"messages": [synthetic_human_message(job.prompt)]}
        config = RunnableConfig(
            recursion_limit=get_config().config.agents.recursion_limit,
            metadata={"workspace_dir": str(proj)},
        )
        if thread_id:
            config["configurable"] = {"thread_id": thread_id}
        response = await agent.graph.ainvoke(inputs, config=config, context=context)
        return extract_output(response)

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
        """广播当前运行中的 run 快照 ``[{job_id, thread_id, started_at}]``。

        带 thread_id：前端既标「运行中」job，也在执行记录顶部显示可点进观测的活条目。
        用 id 而非 name：多机场景下同名任务会串号，id 全局唯一。
        """
        if not self._on_job_status:
            return
        runs = [
            {"job_id": jid, "thread_id": tid, "started_at": ts.isoformat()}
            for jid, (tid, ts) in self._active_runs.items()
        ]
        try:
            self._on_job_status(runs)
        except Exception:
            logger.error("广播任务运行状态失败", exc_info=True)

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
        """将 Job 的 consecutive_errors 持久化到 JobStore。

        notify=False：错误计数是重试退避的内部状态、前端不展示，无需触发 cron.jobs
        广播（否则 flapping 任务每次重试都让所有 desktop 全量重拉任务列表）。
        """
        try:
            await self._job_store.upsert(job, notify=False)
        except Exception:
            logger.warning(
                "更新 consecutive_errors 失败: %s [%s]",
                job.name,
                job.id,
                exc_info=True,
            )

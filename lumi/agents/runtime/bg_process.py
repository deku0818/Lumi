"""后台 Bash 进程管理模块

管理 Bash 后台任务（``run_in_background``）的进程生命周期：启动、监控、超时、
清理。状态由 ``bg_tasks.TaskRegistry`` 统一管理，本模块只持有 ``asyncio`` 进程句柄
与监控协程。

与 ``bg_tasks``（进程无关的元数据注册中心）的分工：注册中心负责"是什么状态"，
本模块负责"怎么跑/怎么停"这些 Bash 进程，复用 ``shell_session`` 的进程生命周期原语。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import IO

from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    NotificationQueue,
    TaskKind,
    TaskStatus,
    bg_tasks_dir,
    get_task_registry,
)
from lumi.agents.runtime.shell_session import (
    _terminate_process,
    get_shell_session_manager,
)
from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TASK_ID_HEX_LENGTH = 12
"""任务 ID 中 UUID hex 截取长度。"""


# ---------------------------------------------------------------------------
# Process handle
# ---------------------------------------------------------------------------


@dataclass
class BashProcessHandle:
    """Bash 后台进程句柄。

    只持有进程管理所需字段，不持有 status/exit_code/error。
    所有状态由 TaskRegistry 统一管理。
    """

    task_id: str
    process: asyncio.subprocess.Process
    timeout: float | None
    """墙钟超时秒数；None 表示不限时（后台任务默认）。"""


# ---------------------------------------------------------------------------
# Background task manager
# ---------------------------------------------------------------------------


class BackgroundTaskManager:
    """后台 Bash 任务管理器。

    管理 Bash 后台任务的进程生命周期：启动、监控、超时、清理。
    所有状态由 TaskRegistry 统一管理，本类只持有进程句柄。
    """

    def __init__(self) -> None:
        self._handles: dict[str, BashProcessHandle] = {}
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._registry = get_task_registry()

    @property
    def notification_queue(self) -> NotificationQueue:
        """获取通知队列（委托给 TaskRegistry）。"""
        return self._registry.notification_queue

    async def start_task(
        self, command: str, timeout: float | None, working_dir: str
    ) -> BackgroundTaskEntry:
        """启动后台 Bash 任务。

        Returns:
            注册到 TaskRegistry 的 BackgroundTaskEntry。

        Raises:
            OSError: 进程启动失败。
        """
        task_id = f"bg_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"

        output_file = bg_tasks_dir() / f"{task_id}.txt"

        output_fd = output_file.open("w")
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=output_fd,
                stderr=asyncio.subprocess.STDOUT,
                cwd=working_dir,
            )
        except OSError:
            output_fd.close()
            raise

        handle = BashProcessHandle(
            task_id=task_id,
            process=process,
            timeout=timeout,
        )
        self._handles[task_id] = handle
        self._monitors[task_id] = asyncio.create_task(
            self._monitor_task(handle, output_fd)
        )

        entry = BackgroundTaskEntry(
            task_id=task_id,
            kind=TaskKind.BASH,
            status=TaskStatus.RUNNING,
            label=command,
            started_at=time.time(),
            output_file=output_file,
        )
        self._registry.register(entry)

        logger.info("[BackgroundTask] 已启动后台任务 %s: %s", task_id, command)
        return entry

    async def cancel_task(self, task_id: str) -> None:
        """取消指定 Bash 任务。

        先更新 registry 状态（避免 monitor finally 发错误通知），
        再取消 monitor 协程，最后 terminate 进程。
        """
        handle = self._handles.get(task_id)
        if handle is None:
            return

        entry = self._registry.get(task_id)
        if entry is None or entry.status != TaskStatus.RUNNING:
            return

        # 先置终态再取消 monitor：monitor 被取消后其 finally 在终态下负责入队通知
        # （见 _monitor_task），此处不再重复入队，否则同一取消通知会进队两次。
        self._registry.update_status(task_id, TaskStatus.FAILED, error="任务被取消")
        await self._cancel_monitor(task_id)
        await _terminate_process(handle.process)

        logger.info("[BackgroundTask] 已取消任务 %s", task_id)

    async def cleanup_all(self) -> None:
        """终止所有运行中的任务并清理进程资源。"""
        await self._cancel_all_monitors()

        for handle in self._handles.values():
            await _terminate_process(handle.process)

        self._handles.clear()
        self._monitors.clear()
        logger.info("[BackgroundTask] 已清理所有后台任务")

    # -- Private helpers --

    async def _cancel_monitor(self, task_id: str) -> None:
        """取消单个监控协程并等待其完成。"""
        monitor = self._monitors.pop(task_id, None)
        if monitor is None or monitor.done():
            return
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass

    async def _cancel_all_monitors(self) -> None:
        """取消所有监控协程并等待它们完成。"""
        monitors_to_await: list[asyncio.Task[None]] = []
        for task_id in list(self._monitors):
            monitor = self._monitors.pop(task_id, None)
            if monitor and not monitor.done():
                monitor.cancel()
                monitors_to_await.append(monitor)
        if monitors_to_await:
            await asyncio.gather(*monitors_to_await, return_exceptions=True)

    async def _monitor_task(
        self, handle: BashProcessHandle, output_fd: IO[str]
    ) -> None:
        """监控后台 Bash 任务，等待完成或超时。

        谁完成谁负责：update_status + enqueue_notification。
        CancelledError 场景由 cancel_task 在调用前已更新状态。
        """
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=handle.timeout)
            exit_code = handle.process.returncode
            if exit_code == 0:
                self._registry.update_status(
                    handle.task_id, TaskStatus.COMPLETED, exit_code=exit_code
                )
            else:
                self._registry.update_status(
                    handle.task_id,
                    TaskStatus.FAILED,
                    exit_code=exit_code,
                    error=f"进程退出码: {exit_code}",
                )
        except TimeoutError:
            await _terminate_process(handle.process)
            self._registry.update_status(
                handle.task_id, TaskStatus.TIMED_OUT, error="超时"
            )
        except asyncio.CancelledError:
            # cancel_task 已在调用前更新了 registry 状态，这里只需关闭 fd
            raise
        except Exception as e:
            self._registry.update_status(
                handle.task_id, TaskStatus.FAILED, error=str(e)
            )
            logger.error(
                "[BackgroundTask] 监控任务 %s 异常: %s",
                handle.task_id,
                e,
                exc_info=True,
            )
        finally:
            try:
                output_fd.close()
            except OSError as e:
                logger.warning(
                    "[BackgroundTask] 关闭输出文件句柄失败 %s: %s",
                    handle.task_id,
                    e,
                )
            # 终态保护：如果已在终态（如 cancel_task 已设置），enqueue_notification 正常工作
            # 如果被 CancelledError 且 cancel_task 未提前设置状态，此处不发通知
            entry = self._registry.get(handle.task_id)
            if entry and entry.status != TaskStatus.RUNNING:
                self._registry.enqueue_notification(handle.task_id)


# ---------------------------------------------------------------------------
# Unified cancel entry
# ---------------------------------------------------------------------------


async def cancel_background_task(task_id: str) -> bool:
    """按 kind 停止一个运行中的后台任务（统一入口）。

    BASH → 经 ``bg_manager.cancel_task``（杀进程）；AGENT / WORKFLOW → 经
    ``registry.cancel_agent_task``（取消 asyncio.Task）。非运行中 / 不存在 → False。

    ws / TUI / background_task 工具共用此函数，避免各自重复 kind 分派（新增 TaskKind
    只改这一处）。返回是否成功发起取消。
    """
    registry = get_task_registry()
    entry = registry.get(task_id)
    if entry is None or entry.status != TaskStatus.RUNNING:
        return False
    if entry.kind == TaskKind.BASH:
        mgr = get_shell_session_manager()
        if not mgr.has_bg_manager:
            return False
        try:
            await mgr.bg_manager.cancel_task(task_id)
        except Exception:
            logger.error(
                "[cancel_background_task] 停止 Bash 失败 %s", task_id, exc_info=True
            )
            return False
        return True
    return registry.cancel_agent_task(task_id)

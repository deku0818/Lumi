"""统一后台任务注册中心

管理所有后台任务（Bash / Agent）的生命周期元数据和通知队列。
TaskRegistry 本身不拥有进程或协程，调用方各自管理执行体。

状态是唯一真相源：所有状态变更必须且只能通过 TaskRegistry.update_status() 进行。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES: frozenset[str] = frozenset()  # populated after TaskStatus


class TaskKind(StrEnum):
    """后台任务类型。"""

    BASH = "bash"
    AGENT = "agent"


class TaskStatus(StrEnum):
    """后台任务状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.TIMED_OUT, TaskStatus.FAILED}
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class BackgroundTaskEntry:
    """统一后台任务条目。

    记录 Bash 和 Agent 两种后台任务的公共元数据。
    ``async_task`` 仅 Agent 类型运行时持有，用于取消。
    """

    task_id: str
    kind: TaskKind
    status: TaskStatus
    label: str
    started_at: float
    output_file: Path
    completed_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    agent_name: str | None = None
    async_task: asyncio.Task | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Notification queue
# ---------------------------------------------------------------------------


class NotificationQueue:
    """后台任务通知队列。

    后台任务完成时将通知 XML 入队，由运行时框架在 Agent 空闲时统一取出注入。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(self, notification_xml: str) -> None:
        """将通知 XML 放入队列（非阻塞）。"""
        self._queue.put_nowait(notification_xml)

    def drain_all(self) -> list[str]:
        """取出队列中所有待发送通知并清空队列。"""
        items: list[str] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def is_empty(self) -> bool:
        """队列是否为空。"""
        return self._queue.empty()


# ---------------------------------------------------------------------------
# Notification formatting
# ---------------------------------------------------------------------------


def format_notification(entry: BackgroundTaskEntry) -> str:
    """将后台任务条目格式化为 task-notification XML 字符串。"""
    match entry.kind:
        case TaskKind.BASH:
            summary = _format_bash_summary(entry)
        case TaskKind.AGENT:
            summary = _format_agent_summary(entry)
        case _:
            summary = f'未知任务类型 "{entry.kind}" (label={entry.label}), 状态: {entry.status}'
            logger.warning("[format_notification] 未知任务类型: %s", entry.kind)

    return (
        "<task-notification>\n"
        f"  <task-id>{entry.task_id}</task-id>\n"
        f"  <task-kind>{entry.kind}</task-kind>\n"
        f"  <status>{entry.status}</status>\n"
        f"  <output-file>{entry.output_file.resolve()}</output-file>\n"
        f"  <summary>{summary}</summary>\n"
        "</task-notification>"
    )


def _format_bash_summary(entry: BackgroundTaskEntry) -> str:
    match entry.status:
        case TaskStatus.COMPLETED:
            return f'命令 "{entry.label}" 已完成，退出码 {entry.exit_code}'
        case TaskStatus.TIMED_OUT:
            return f'命令 "{entry.label}" 超时'
        case _:
            return f'命令 "{entry.label}" 失败，退出码 {entry.exit_code}'


def _format_agent_summary(entry: BackgroundTaskEntry) -> str:
    name = entry.agent_name or entry.label
    match entry.status:
        case TaskStatus.COMPLETED:
            return f'代理 "{name}" 已完成'
        case TaskStatus.FAILED:
            error_hint = f": {entry.error}" if entry.error else ""
            return f'代理 "{name}" 失败{error_hint}'
        case _:
            return f'代理 "{name}" 状态: {entry.status}'


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------


class TaskRegistry:
    """统一后台任务注册中心。

    状态唯一真相源：所有状态变更必须通过 update_status() 进行。
    不拥有进程或协程，调用方各自管理执行体。
    """

    def __init__(self) -> None:
        self._entries: dict[str, BackgroundTaskEntry] = {}
        self._notification_queue = NotificationQueue()

    @property
    def notification_queue(self) -> NotificationQueue:
        return self._notification_queue

    def register(self, entry: BackgroundTaskEntry) -> None:
        """注册新的后台任务。重复 task_id 会抛出 ValueError。"""
        if entry.task_id in self._entries:
            raise ValueError(f"重复的 task_id: {entry.task_id}")
        self._entries[entry.task_id] = entry
        logger.info(
            "[TaskRegistry] 注册任务 %s (kind=%s, label=%s)",
            entry.task_id,
            entry.kind,
            entry.label,
        )

    def get(self, task_id: str) -> BackgroundTaskEntry | None:
        """查询任务。"""
        return self._entries.get(task_id)

    def all_tasks(self) -> list[BackgroundTaskEntry]:
        """返回所有任务（按启动时间排序）。"""
        return sorted(self._entries.values(), key=lambda e: e.started_at)

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> None:
        """更新任务状态（唯一写入点）。终态不可被覆盖。"""
        entry = self._entries.get(task_id)
        if entry is None:
            logger.warning("[TaskRegistry] 更新不存在的任务: %s", task_id)
            return
        if entry.status in _TERMINAL_STATUSES:
            logger.debug(
                "[TaskRegistry] 任务 %s 已处于终态 %s，忽略更新为 %s",
                task_id,
                entry.status,
                status,
            )
            return
        entry.status = status
        entry.completed_at = time.time()
        if exit_code is not None:
            entry.exit_code = exit_code
        if error is not None:
            entry.error = error
        logger.debug("[TaskRegistry] 任务 %s → %s", task_id, status)

    def enqueue_notification(self, task_id: str) -> None:
        """为指定任务生成通知 XML 并入队。"""
        entry = self._entries.get(task_id)
        if entry is None:
            logger.warning("[TaskRegistry] 通知入队失败，任务不存在: %s", task_id)
            return
        try:
            xml = format_notification(entry)
            self._notification_queue.enqueue(xml)
        except Exception:
            logger.error(
                "[TaskRegistry] 通知入队异常 (task_id=%s, kind=%s, status=%s)",
                task_id,
                entry.kind,
                entry.status,
                exc_info=True,
            )

    def cancel_agent_task(self, task_id: str) -> bool:
        """请求取消 Agent 后台任务。

        只调用 task.cancel()，不直接修改 status。
        状态更新由协程的 CancelledError handler 负责。
        返回是否成功发起取消请求。
        """
        entry = self._entries.get(task_id)
        if entry is None or entry.status != TaskStatus.RUNNING:
            return False
        if entry.kind != TaskKind.AGENT or entry.async_task is None:
            return False
        entry.async_task.cancel()
        logger.info("[TaskRegistry] 已请求取消 Agent 任务 %s", task_id)
        return True

    def cleanup(self) -> None:
        """清理：取消所有运行中的 Agent 任务并发送通知，然后清空条目。"""
        for entry in self._entries.values():
            if (
                entry.status == TaskStatus.RUNNING
                and entry.kind == TaskKind.AGENT
                and entry.async_task is not None
            ):
                entry.async_task.cancel()
                entry.status = TaskStatus.FAILED
                entry.error = "清理时终止"
                entry.completed_at = time.time()
                self.enqueue_notification(entry.task_id)
        self._entries.clear()
        logger.info("[TaskRegistry] 已清理所有任务条目")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: TaskRegistry | None = None


def get_task_registry() -> TaskRegistry:
    """获取全局 TaskRegistry 单例。"""
    global _registry
    if _registry is None:
        _registry = TaskRegistry()
    return _registry

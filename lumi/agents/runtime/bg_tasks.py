"""统一后台任务注册中心

管理所有后台任务（Bash / Agent）的生命周期元数据和通知队列。
TaskRegistry 本身不拥有进程或协程，调用方各自管理执行体。

状态是唯一真相源：所有状态变更必须且只能通过 TaskRegistry.update_status() 进行。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import Path

from lumi.utils.logger import logger
from lumi.utils.paths import lumi_tmp_dir

# 当前轮所属的 LangGraph thread_id：由 bridge / cron scheduler 在执行前设置，
# 后台任务注册时捕获为归属标记，使完成通知能路由回正确的会话（多 WS 连接场景）
current_thread_id: ContextVar[str] = ContextVar("current_thread_id", default="")


def bg_tasks_dir() -> Path:
    """后台任务输出文件落地目录（task_id 全局唯一不冲突，不污染工作区）。"""
    return lumi_tmp_dir("bg_tasks")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES: frozenset[str] = frozenset()  # populated after TaskStatus


class TaskKind(StrEnum):
    """后台任务类型。"""

    BASH = "bash"
    AGENT = "agent"
    WORKFLOW = "workflow"


class TaskStatus(StrEnum):
    """后台任务状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.TIMED_OUT, TaskStatus.FAILED}
)

# 每会话终态任务保留上限：超出时自动丢弃最旧的（防长会话无限堆积 + 内存涨）。
# 运行中任务永不自动清。
_TERMINAL_CAP = 20

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
    prompt: str = ""
    # Workflow 任务跑完的子代理扇出数（仅 WORKFLOW 类型有意义，用于通知摘要）
    agent_count: int | None = None
    # Workflow 实时进度快照（phase / 计数 / 在跑窗口），由引擎经 notify_progress 更新；
    # 其余 kind 为 None。形状由前端 drawer 详情消费，后端只透传不解释。
    progress: dict | None = None
    # 任务所属的 LangGraph thread_id（注册时从 current_thread_id 捕获），
    # 完成通知按此归属投递；空串表示无归属（任一会话可认领）
    thread_id: str = ""
    async_task: asyncio.Task | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Notification queue
# ---------------------------------------------------------------------------


class NotificationQueue:
    """后台任务通知队列。

    后台任务完成时将通知 XML 入队（附归属 thread_id），由运行时框架在
    Agent 空闲时取出注入。多前端连接场景按 thread_id 认领，避免通知被
    无关会话抢走。
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []  # (thread_id, xml)

    def enqueue(self, notification_xml: str, thread_id: str = "") -> None:
        """将通知 XML 放入队列（非阻塞）。"""
        self._items.append((thread_id, notification_xml))

    def drain_all(self) -> list[str]:
        """取出全部待发送通知并清空队列（单会话前端，如 TUI）。"""
        items = [xml for _, xml in self._items]
        self._items.clear()
        return items

    def drain_for(self, thread_id: str) -> list[str]:
        """取出归属指定 thread（或无归属）的通知，其余留在队列中等待各自会话认领。"""
        taken = [xml for owner, xml in self._items if owner in ("", thread_id)]
        if taken:
            self._items = [
                (owner, xml)
                for owner, xml in self._items
                if owner not in ("", thread_id)
            ]
        return taken

    def is_empty(self) -> bool:
        """队列是否为空。"""
        return not self._items


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
        case TaskKind.WORKFLOW:
            summary = _format_workflow_summary(entry)
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


def safe_write_output(output_file: Path, content: str, task_id: str) -> None:
    """安全写入后台任务输出文件，失败只记日志不抛异常（agent / workflow 后台共用）。"""
    try:
        output_file.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning("[bg_task] 写入输出文件失败 %s: %s", task_id, e)


def make_bg_done_callback(
    task_id: str, log_prefix: str
) -> Callable[[asyncio.Task], None]:
    """后台 asyncio.Task 的 done-callback：忽略取消，记录未处理异常（agent / workflow 共用）。"""

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "[%s] 未处理的异常 task %s: %s", log_prefix, task_id, exc, exc_info=exc
            )

    return _on_done


async def run_background_task(
    task_id: str,
    output_file: Path,
    produce: Callable[[], Awaitable[str]],
    *,
    cancel_text: str,
) -> None:
    """后台任务收尾骨架（agent / workflow 共用）：``produce()`` 跑出成功文本 → 写文件 +
    置 COMPLETED；取消 / 异常写占位文本 + 置 FAILED；finally 入队完成通知。

    谁完成谁负责（update_status + enqueue_notification）。各调用方只提供 ``produce``
    协程与 ``cancel_text``，差异化只剩成功路径。
    """
    registry = get_task_registry()
    try:
        safe_write_output(output_file, await produce(), task_id)
        registry.update_status(task_id, TaskStatus.COMPLETED)
    except asyncio.CancelledError:
        safe_write_output(output_file, cancel_text, task_id)
        registry.update_status(task_id, TaskStatus.FAILED, error="任务被取消")
        raise
    except Exception as e:
        safe_write_output(output_file, f"Error: {e}", task_id)
        registry.update_status(task_id, TaskStatus.FAILED, error=str(e))
        logger.error("[bg_task] task %s failed: %s", task_id, e, exc_info=True)
    finally:
        registry.enqueue_notification(task_id)


# serialize_task 不外发的内部字段：async_task 不可 JSON 化、prompt 可能很大且前端不用。
_WIRE_EXCLUDE = frozenset({"async_task", "prompt"})


def serialize_task(entry: BackgroundTaskEntry) -> dict:
    """把后台任务条目序列化为前端 drawer 消费的 dict（wire 安全：仅原始类型）。

    从 dataclass 字段派生（排除内部字段）：新增字段默认随之上线，不会因漏改这里被静默
    丢弃；枚举 / Path 强转为字符串。前端 BgTask 类型是唯一的「该不该收」的刻意闸门。
    """
    data = {
        f.name: getattr(entry, f.name)
        for f in fields(entry)
        if f.name not in _WIRE_EXCLUDE
    }
    data["kind"] = str(data["kind"])
    data["status"] = str(data["status"])
    data["output_file"] = str(data["output_file"])
    return data


def _format_workflow_summary(entry: BackgroundTaskEntry) -> str:
    name = entry.agent_name or entry.label
    match entry.status:
        case TaskStatus.COMPLETED:
            fanout = (
                f"（{entry.agent_count} 个子代理）"
                if entry.agent_count is not None
                else ""
            )
            return f'工作流 "{name}" 已完成{fanout}'
        case TaskStatus.FAILED:
            error_hint = f": {entry.error}" if entry.error else ""
            return f'工作流 "{name}" 失败{error_hint}'
        case _:
            return f'工作流 "{name}" 状态: {entry.status}'


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
        # 状态/进度变更观察者：server 层注册，把变更广播给 desktop drawer（同 cron
        # 的 on_job_status 模式）。TUI / 测试不设 → 不广播。同步回调，内部自行调度异步。
        self._on_change: Callable[[], None] | None = None

    @property
    def notification_queue(self) -> NotificationQueue:
        return self._notification_queue

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        """注册变更观察者（register / update_status / notify_progress 后触发）。"""
        self._on_change = callback

    def _fire_change(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            logger.error("[TaskRegistry] on_change 回调异常", exc_info=True)

    def notify_progress(self, task_id: str, progress: dict) -> None:
        """更新任务实时进度快照并通知观察者（Workflow 引擎用）。"""
        entry = self._entries.get(task_id)
        if entry is None:
            return
        entry.progress = progress
        self._fire_change()

    def register(self, entry: BackgroundTaskEntry) -> None:
        """注册新的后台任务。重复 task_id 会抛出 ValueError。"""
        if entry.task_id in self._entries:
            raise ValueError(f"重复的 task_id: {entry.task_id}")
        if not entry.thread_id:
            entry.thread_id = current_thread_id.get()
        self._entries[entry.task_id] = entry
        logger.info(
            "[TaskRegistry] 注册任务 %s (kind=%s, label=%s)",
            entry.task_id,
            entry.kind,
            entry.label,
        )
        self._fire_change()

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
        if status in _TERMINAL_STATUSES:
            self._trim_terminal(entry.thread_id)
        self._fire_change()

    def enqueue_notification(self, task_id: str) -> None:
        """为指定任务生成通知 XML 并入队。"""
        entry = self._entries.get(task_id)
        if entry is None:
            logger.warning("[TaskRegistry] 通知入队失败，任务不存在: %s", task_id)
            return
        try:
            xml = format_notification(entry)
            self._notification_queue.enqueue(xml, entry.thread_id)
        except Exception:
            logger.error(
                "[TaskRegistry] 通知入队异常 (task_id=%s, kind=%s, status=%s)",
                task_id,
                entry.kind,
                entry.status,
                exc_info=True,
            )

    def cancel_agent_task(self, task_id: str) -> bool:
        """请求取消持有 async_task 的后台任务（Agent / Workflow）。

        只调用 task.cancel()，不直接修改 status。
        状态更新由协程的 CancelledError handler 负责。
        返回是否成功发起取消请求。
        """
        entry = self._entries.get(task_id)
        if entry is None or entry.status != TaskStatus.RUNNING:
            return False
        if entry.kind not in (TaskKind.AGENT, TaskKind.WORKFLOW) or (
            entry.async_task is None
        ):
            return False
        entry.async_task.cancel()
        logger.info("[TaskRegistry] 已请求取消 %s 任务 %s", entry.kind, task_id)
        return True

    def dismiss(self, task_id: str) -> bool:
        """从列表移除一个**终态**任务（运行中不可移除）。返回是否移除。

        只删注册表条目（输出文件留盘）；用户主动清理 drawer 用。
        """
        entry = self._entries.get(task_id)
        if entry is None or entry.status not in _TERMINAL_STATUSES:
            return False
        del self._entries[task_id]
        self._fire_change()
        return True

    def clear_finished(self, thread_id: str | None = None) -> int:
        """批量移除终态任务（``thread_id`` 限定会话，None=全部）。返回移除数。"""
        drop = [
            tid
            for tid, e in self._entries.items()
            if e.status in _TERMINAL_STATUSES
            and (thread_id is None or e.thread_id == thread_id)
        ]
        for tid in drop:
            del self._entries[tid]
        if drop:
            self._fire_change()
        return len(drop)

    def _trim_terminal(self, thread_id: str) -> None:
        """把该会话的终态任务裁到 ``_TERMINAL_CAP``，丢弃最旧的（按完成/启动时间）。"""
        terminal = [
            e
            for e in self._entries.values()
            if e.thread_id == thread_id and e.status in _TERMINAL_STATUSES
        ]
        if len(terminal) <= _TERMINAL_CAP:
            return
        terminal.sort(key=lambda e: e.completed_at or e.started_at)
        for e in terminal[: len(terminal) - _TERMINAL_CAP]:
            del self._entries[e.task_id]

    def cleanup(self) -> None:
        """清理：取消所有运行中的 Agent 任务并发送通知，然后清空条目。"""
        for entry in self._entries.values():
            if (
                entry.status == TaskStatus.RUNNING
                and entry.kind in (TaskKind.AGENT, TaskKind.WORKFLOW)
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

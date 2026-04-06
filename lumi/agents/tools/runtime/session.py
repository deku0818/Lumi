"""本地 Shell 会话管理模块

提供持久化的本地 shell 会话，用于 bash 工具和技能命令执行。
通过 stdin/stdout 与后台 shell 进程通信，支持跨调用保持环境状态。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO

from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SENTINEL_PREFIX = "__LUMI_SENTINEL_"
"""命令输出边界标记前缀，与随机后缀组合保证唯一性。"""

_SENTINEL_SUFFIX = "__"
"""命令输出边界标记后缀。"""

_DEFAULT_COMMAND_TIMEOUT: float = 120.0
"""execute() 默认超时秒数。"""

_CWD_QUERY_TIMEOUT: float = 5.0
"""get_cwd() 查询超时秒数。"""

_GRACEFUL_SHUTDOWN_TIMEOUT: float = 5.0
"""进程优雅关闭等待秒数（terminate 后等待退出的时长）。"""

_BG_TASKS_DIR = ".lumi/bg_tasks"
"""后台任务输出文件相对目录。"""

_TASK_ID_HEX_LENGTH = 12
"""任务 ID 中 UUID hex 截取长度。"""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommandResult:
    """命令执行结果（不可变）。"""

    stdout: str
    exit_code: int
    success: bool
    timed_out: bool


class TaskStatus(StrEnum):
    """后台任务状态枚举。"""

    RUNNING = "running"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass
class BackgroundTask:
    """后台任务数据模型。

    记录后台任务的完整生命周期信息，包括进程句柄、状态和输出文件路径。
    task_id 格式为 ``bg_{uuid_hex[:12]}``。
    """

    task_id: str
    command: str
    status: TaskStatus
    output_file: Path
    process: asyncio.subprocess.Process
    started_at: float
    timeout: float
    exit_code: int | None = None
    error: str | None = None


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


def format_task_notification(task: BackgroundTask) -> str:
    """将后台任务状态格式化为 task-notification XML 字符串。"""
    match task.status:
        case TaskStatus.COMPLETED:
            summary = f'命令 "{task.command}" 已完成，退出码 {task.exit_code}'
        case TaskStatus.TIMED_OUT:
            summary = f'命令 "{task.command}" 超时'
        case _:
            summary = f'命令 "{task.command}" 失败，退出码 {task.exit_code}'

    return (
        "<task-notification>\n"
        f"  <task-id>{task.task_id}</task-id>\n"
        f"  <status>{task.status}</status>\n"
        f"  <output-file>{task.output_file.resolve()}</output-file>\n"
        f"  <summary>{summary}</summary>\n"
        "</task-notification>"
    )


# ---------------------------------------------------------------------------
# Process lifecycle helpers
# ---------------------------------------------------------------------------


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """终止进程：先 terminate()，超时后 kill()。"""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=_GRACEFUL_SHUTDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except (ProcessLookupError, OSError) as e:
            logger.debug(f"[BackgroundTask] kill/wait 异常（进程可能已退出）: {e}")
    except ProcessLookupError:
        logger.debug("[BackgroundTask] 进程已退出，无需终止")


async def _close_process_transport(proc: asyncio.subprocess.Process) -> None:
    """显式关闭子进程 transport。

    避免事件循环关闭时 ``BaseSubprocessTransport.__del__``
    触发 ``RuntimeError('Event loop is closed')``。
    """
    transport = getattr(proc, "_transport", None)
    if transport is not None and not transport.is_closing():
        transport.close()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Background task manager
# ---------------------------------------------------------------------------


class BackgroundTaskManager:
    """后台任务管理器。

    管理后台任务的生命周期：启动、监控、超时、清理。
    通过独立子进程执行后台命令，避免与前台 shell 会话的锁竞争。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._notification_queue = NotificationQueue()

    @property
    def notification_queue(self) -> NotificationQueue:
        """获取通知队列。"""
        return self._notification_queue

    async def start_task(
        self, command: str, timeout: float, working_dir: str
    ) -> BackgroundTask:
        """启动后台任务。

        通过 ``asyncio.create_subprocess_shell()`` 启动独立子进程，
        输出重定向到 ``.lumi/bg_tasks/{task_id}.txt``。

        Raises:
            OSError: 进程启动失败。
        """
        task_id = f"bg_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"

        output_dir = Path(working_dir) / _BG_TASKS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{task_id}.txt"

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

        task = BackgroundTask(
            task_id=task_id,
            command=command,
            status=TaskStatus.RUNNING,
            output_file=output_file,
            process=process,
            started_at=time.time(),
            timeout=timeout,
        )

        self._tasks[task_id] = task
        self._monitors[task_id] = asyncio.create_task(
            self._monitor_task(task, output_fd)
        )

        logger.info(f"[BackgroundTask] 已启动后台任务 {task_id}: {command}")
        return task

    def get_task(self, task_id: str) -> BackgroundTask | None:
        """查询任务状态。"""
        return self._tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> None:
        """取消指定任务。

        先取消监控协程并等待其 finally 完成（关闭 fd），
        再 terminate 进程，最后设置状态。
        """
        task = self._tasks.get(task_id)
        if task is None or task.status != TaskStatus.RUNNING:
            return

        await self._cancel_monitor(task_id)
        await _terminate_process(task.process)
        task.status = TaskStatus.FAILED
        task.error = "任务被取消"

        logger.info(f"[BackgroundTask] 已取消任务 {task_id}")

    async def cleanup_all(self) -> None:
        """终止所有运行中的任务，清理输出文件和进程资源。"""
        await self._cancel_all_monitors()

        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING:
                await _terminate_process(task.process)
                task.status = TaskStatus.FAILED
                task.error = "清理时终止"
            self._cleanup_output_file(task)

        self._tasks.clear()
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

    async def _monitor_task(self, task: BackgroundTask, output_fd: IO[str]) -> None:
        """监控后台任务，等待完成或超时。

        任务完成后生成通知 XML 入队到 NotificationQueue。
        """
        try:
            await self._wait_for_completion(task)
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMED_OUT
            await _terminate_process(task.process)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(
                f"[BackgroundTask] 监控任务 {task.task_id} 异常: {e}",
                exc_info=True,
            )
        finally:
            try:
                output_fd.close()
            except OSError as e:
                logger.warning(
                    f"[BackgroundTask] 关闭输出文件句柄失败 {task.task_id}: {e}"
                )
            self._enqueue_notification(task)

    @staticmethod
    async def _wait_for_completion(task: BackgroundTask) -> None:
        """等待任务进程完成并更新状态。"""
        await asyncio.wait_for(task.process.wait(), timeout=task.timeout)
        exit_code = task.process.returncode
        task.exit_code = exit_code
        if exit_code == 0:
            task.status = TaskStatus.COMPLETED
        else:
            task.status = TaskStatus.FAILED
            task.error = f"进程退出码: {exit_code}"

    def _enqueue_notification(self, task: BackgroundTask) -> None:
        """生成通知 XML 并入队。"""
        try:
            xml = format_task_notification(task)
            self._notification_queue.enqueue(xml)
        except Exception as e:
            logger.error(f"[BackgroundTask] 通知入队失败: {e}")

    @staticmethod
    def _cleanup_output_file(task: BackgroundTask) -> None:
        """删除任务输出文件。"""
        try:
            if task.output_file.exists():
                task.output_file.unlink()
        except OSError as e:
            logger.warning(f"[BackgroundTask] 删除输出文件失败 {task.output_file}: {e}")


# ---------------------------------------------------------------------------
# Local shell session
# ---------------------------------------------------------------------------


def _make_sentinel() -> str:
    """生成唯一哨兵标记，用于区分命令输出边界。"""
    return (
        f"{_SENTINEL_PREFIX}{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}{_SENTINEL_SUFFIX}"
    )


class LocalShellSession:
    """本地持久化 shell 会话。

    通过 stdin/stdout 与后台 shell 进程通信。
    支持 cd、export、alias 等环境状态跨调用保持。
    Windows 下使用 cmd.exe，Unix 下使用 bash。
    """

    _is_windows: bool = sys.platform == "win32"

    def __init__(self, working_dir: str | None = None) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._working_dir: str = working_dir or os.getcwd()
        self._lock = asyncio.Lock()

    # -- Process management --

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        """确保 shell 进程存在且运行中。"""
        if self._process is not None and self._process.returncode is None:
            return self._process

        self._process = await self._spawn_shell()
        return self._process

    async def _spawn_shell(self) -> asyncio.subprocess.Process:
        """启动新的 shell 进程。"""
        if self._is_windows:
            return await asyncio.create_subprocess_exec(
                "cmd.exe",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._working_dir,
            )
        return await asyncio.create_subprocess_shell(
            "/bin/bash --norc --noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._working_dir,
            env={**os.environ, "TERM": "dumb", "PS1": "", "PS2": ""},
        )

    # -- Command execution --

    async def get_cwd(self) -> str:
        """获取当前会话的实际工作目录。

        通过在 shell 中执行 pwd 命令获取，反映 cd 命令后的真实路径。
        如果查询失败，回退到初始工作目录。
        """
        result = await self.execute("pwd", timeout=_CWD_QUERY_TIMEOUT)
        if result.success and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(
            f"[LocalShellSession] pwd 失败 (exit_code={result.exit_code})，"
            f"回退到初始目录: {self._working_dir}"
        )
        return self._working_dir

    async def execute(
        self, command: str, timeout: float = _DEFAULT_COMMAND_TIMEOUT
    ) -> CommandResult:
        """执行命令并返回结果。

        Args:
            command: 要执行的 shell 命令。
            timeout: 超时时间（秒）。

        Returns:
            包含 stdout、exit_code、success、timed_out 的结果。
        """
        async with self._lock:
            return await self._execute_locked(command, timeout)

    async def _execute_locked(self, command: str, timeout: float) -> CommandResult:
        """在持有锁的状态下执行命令（内部实现）。"""
        try:
            process = await self._ensure_process()
        except OSError as e:
            return CommandResult(
                stdout=f"无法启动 shell 进程: {e}",
                exit_code=-1,
                success=False,
                timed_out=False,
            )

        assert process.stdin is not None
        assert process.stdout is not None

        sentinel = _make_sentinel()
        wrapped = self._wrap_command(command, sentinel)

        process.stdin.write(wrapped.encode())
        await process.stdin.drain()

        return await self._read_until_sentinel(process, sentinel, timeout)

    def _wrap_command(self, command: str, sentinel: str) -> str:
        """将用户命令包装为带哨兵标记和退出码的 shell 脚本片段。"""
        if self._is_windows:
            return f"{command}\r\necho.\r\necho {sentinel} %ERRORLEVEL%\r\n"
        return f'{command}\n__lumi_ec=$?\necho ""\necho "{sentinel} $__lumi_ec"\n'

    async def _read_until_sentinel(
        self,
        process: asyncio.subprocess.Process,
        sentinel: str,
        timeout: float,
    ) -> CommandResult:
        """读取进程输出直到遇到哨兵标记或超时。"""
        assert process.stdout is not None
        output_lines: list[str] = []
        exit_code = -1

        try:
            exit_code = await self._collect_output(
                process.stdout, sentinel, output_lines, timeout
            )
            return CommandResult(
                stdout="\n".join(output_lines),
                exit_code=exit_code,
                success=(exit_code == 0),
                timed_out=False,
            )
        except asyncio.TimeoutError:
            await self._handle_timeout(process)
            return CommandResult(
                stdout="\n".join(output_lines),
                exit_code=exit_code,
                success=False,
                timed_out=True,
            )

    @staticmethod
    async def _collect_output(
        stdout: asyncio.StreamReader,
        sentinel: str,
        output_lines: list[str],
        timeout: float,
    ) -> int:
        """从 stdout 逐行收集输出，返回解析到的退出码。

        Raises:
            asyncio.TimeoutError: 读取超时。
        """
        exit_code = -1
        while True:
            line_bytes = await asyncio.wait_for(stdout.readline(), timeout=timeout)
            if not line_bytes:
                break

            line = (
                line_bytes.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
            )

            if sentinel not in line:
                output_lines.append(line)
                continue

            # 解析退出码
            parts = line.split(sentinel)
            if len(parts) >= 2:
                code_str = parts[1].strip()
                try:
                    exit_code = int(code_str)
                except ValueError:
                    pass
            break

        return exit_code

    async def _handle_timeout(self, process: asyncio.subprocess.Process) -> None:
        """超时后杀掉进程并清理 transport。"""
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await _close_process_transport(process)
        self._process = None

    # -- Lifecycle --

    async def close(self) -> None:
        """关闭 shell 会话。"""
        proc = self._process
        if proc is None:
            return

        if proc.returncode is None:
            await self._graceful_shutdown(proc)

        await _close_process_transport(proc)
        self._process = None

    @staticmethod
    async def _graceful_shutdown(proc: asyncio.subprocess.Process) -> None:
        """尝试通过 exit 命令优雅关闭，超时则 kill。"""
        try:
            assert proc.stdin is not None
            proc.stdin.write(b"exit\n")
            await proc.stdin.drain()
            await asyncio.wait_for(proc.wait(), timeout=_GRACEFUL_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, BrokenPipeError, OSError):
            pass


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class SessionManager:
    """管理多个本地 shell 会话。

    为不同的 thread_id 维护独立的持久化 shell 会话。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LocalShellSession] = {}
        self._bg_manager: BackgroundTaskManager | None = None

    @property
    def bg_manager(self) -> BackgroundTaskManager:
        """获取后台任务管理器（懒初始化）。"""
        if self._bg_manager is None:
            self._bg_manager = BackgroundTaskManager()
        return self._bg_manager

    @property
    def has_bg_manager(self) -> bool:
        """后台任务管理器是否已初始化。"""
        return self._bg_manager is not None

    def get_session(
        self, thread_id: str, working_dir: str | None = None
    ) -> LocalShellSession:
        """获取或创建持久化会话。

        Args:
            thread_id: 线程标识符。
            working_dir: 工作目录（仅在创建新会话时生效）。
        """
        if thread_id not in self._sessions:
            self._sessions[thread_id] = LocalShellSession(working_dir)
            logger.debug(f"为线程 {thread_id} 创建新的 shell 会话")
        return self._sessions[thread_id]

    async def close_session(self, thread_id: str) -> None:
        """关闭指定线程的会话。"""
        if thread_id not in self._sessions:
            return
        await self._sessions[thread_id].close()
        del self._sessions[thread_id]
        logger.debug(f"已关闭线程 {thread_id} 的 shell 会话")

    async def close_all(self) -> None:
        """关闭所有会话并清理后台任务。"""
        if self._bg_manager is not None:
            await self._bg_manager.cleanup_all()
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        logger.debug("已关闭所有 shell 会话")


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager

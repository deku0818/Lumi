"""本地 Shell 会话管理模块

提供持久化的本地 shell 会话，用于 bash 工具和技能命令执行。
通过 stdin/stdout 与后台 shell 进程通信，支持跨调用保持环境状态。
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lumi.agents.runtime.bg_tasks import (
    NotificationQueue,
    TaskStatus,
    get_task_registry,
)

if TYPE_CHECKING:
    from lumi.agents.runtime.bg_process import BackgroundTaskManager
from lumi.utils.constants import (
    BASH_MAX_OUTPUT_BYTES,
    CWD_QUERY_TIMEOUT,
    DEFAULT_COMMAND_TIMEOUT,
    GRACEFUL_SHUTDOWN_TIMEOUT,
)
from lumi.utils.logger import logger

# Re-export for backward compatibility
__all__ = ["TaskStatus", "NotificationQueue"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SENTINEL_PREFIX = "__LUMI_SENTINEL_"
"""命令输出边界标记前缀，与随机后缀组合保证唯一性。"""

_SENTINEL_SUFFIX = "__"
"""命令输出边界标记后缀。"""

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


class _BoundedOutputBuffer:
    """字节级流式累加器：保头丢尾。

    用于 LocalShellSession._collect_output 实时限制内存占用。
    超过 max_bytes 后的整行丢弃（不部分截断行），累计 dropped_bytes 供 trailer 使用。
    """

    __slots__ = ("_lines", "_bytes_used", "_dropped_bytes", "_max_bytes")

    def __init__(self, max_bytes: int) -> None:
        self._lines: list[str] = []
        self._bytes_used: int = 0
        self._dropped_bytes: int = 0
        self._max_bytes: int = max_bytes

    def append(self, line: str) -> None:
        # +1 预留 __str__ join 时插入的换行符
        line_bytes = len(line.encode("utf-8", errors="replace")) + 1
        if self._bytes_used + line_bytes <= self._max_bytes:
            self._lines.append(line)
            self._bytes_used += line_bytes
        else:
            self._dropped_bytes += line_bytes

    def __str__(self) -> str:
        text = "\n".join(self._lines)
        if self._dropped_bytes > 0:
            kb = self._dropped_bytes // 1024 or 1
            text += f"\n... [output truncated - {kb} KB dropped]"
        return text


# ---------------------------------------------------------------------------
# Process lifecycle helpers
# ---------------------------------------------------------------------------


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """终止进程：先 terminate()，超时后 kill()。"""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=GRACEFUL_SHUTDOWN_TIMEOUT)
    except TimeoutError:
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
        result = await self.execute("pwd", timeout=CWD_QUERY_TIMEOUT)
        if result.success and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(
            f"[LocalShellSession] pwd 失败 (exit_code={result.exit_code})，"
            f"回退到初始目录: {self._working_dir}"
        )
        return self._working_dir

    async def execute(
        self, command: str, timeout: float = DEFAULT_COMMAND_TIMEOUT
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
        buffer = _BoundedOutputBuffer(BASH_MAX_OUTPUT_BYTES)
        exit_code = -1

        try:
            exit_code = await self._collect_output(
                process.stdout, sentinel, buffer, timeout
            )
            return CommandResult(
                stdout=str(buffer),
                exit_code=exit_code,
                success=(exit_code == 0),
                timed_out=False,
            )
        except TimeoutError:
            await self._handle_timeout(process)
            return CommandResult(
                stdout=str(buffer),
                exit_code=exit_code,
                success=False,
                timed_out=True,
            )

    @staticmethod
    async def _collect_output(
        stdout: asyncio.StreamReader,
        sentinel: str,
        buffer: _BoundedOutputBuffer,
        timeout: float,
    ) -> int:
        """从 stdout 逐行收集输出到 buffer，返回解析到的退出码。

        buffer 超限后续行会被丢弃，但循环会持续读取以消费 pipe
        直到遇到 sentinel — 避免 shell 因 stdout pipe 未被消费而阻塞。

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
                buffer.append(line)
                continue

            # sentinel 行不进 buffer —— 保证 exit code 解析不受截断影响
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
            await asyncio.wait_for(proc.wait(), timeout=GRACEFUL_SHUTDOWN_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, BrokenPipeError, OSError):
            pass


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class ShellSessionManager:
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
            from lumi.agents.runtime.bg_process import BackgroundTaskManager

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
        get_task_registry().cleanup()
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        logger.debug("已关闭所有 shell 会话")


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_session_manager: ShellSessionManager | None = None


def get_shell_session_manager() -> ShellSessionManager:
    """获取全局会话管理器单例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = ShellSessionManager()
    return _session_manager


# ---------------------------------------------------------------------------
# 子代理独立 shell 作用域
# ---------------------------------------------------------------------------

# 子代理专属 shell 会话键。空串 = 非子代理上下文，bash 退回 thread 级 shell。
_current_shell_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "lumi_current_shell_key", default=""
)


def current_shell_key() -> str:
    """当前上下文的 shell 会话键：子代理作用域内为其专属键，否则空串。"""
    return _current_shell_key.get()


async def run_with_shell(key: str, coro):
    """在「独立 shell 作用域」里执行 coro（子代理用），返回其结果。

    在 copy_context 副本里把 shell 键设为 key 再跑——本作用域内的 bash 取到专属 shell，
    与父 / 兄弟代理的 shell 互不串（cd/env 不外溢）；因在副本上下文执行，并发的兄弟
    子代理（ToolNode 同批 gather 共用上下文）也各自隔离。结束后回收该 shell（用完即弃，
    避免按子代理累积孤儿 bash 进程）。

    不继承父 shell 的 cwd/env：子代理 shell 在本会话项目根惰性新建（working_dir 取
    per-run 授权主目录）——简单且足够。
    """
    ctx = contextvars.copy_context()
    ctx.run(_current_shell_key.set, key)
    task = asyncio.create_task(coro, context=ctx)
    try:
        return await task
    finally:
        if not task.done():
            task.cancel()
        await get_shell_session_manager().close_session(key)

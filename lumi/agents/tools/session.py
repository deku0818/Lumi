"""本地 Shell 会话管理模块

提供持久化的本地 shell 会话，用于 bash 工具和技能命令执行。
通过 stdin/stdout 与后台 shell 进程通信，支持跨调用保持环境状态。
"""

import asyncio
import os
import sys
import uuid
from dataclasses import dataclass

from lumi.utils.logger import logger


@dataclass
class CommandResult:
    """命令执行结果"""

    stdout: str
    exit_code: int
    success: bool
    timed_out: bool


class LocalShellSession:
    """本地持久化 shell 会话

    通过 stdin/stdout 与后台 shell 进程通信。
    支持 cd、export、alias 等环境状态跨调用保持。
    Windows 下使用 cmd.exe，Unix 下使用 bash。
    """

    def __init__(self, working_dir: str | None = None):
        self._process: asyncio.subprocess.Process | None = None
        self._working_dir = working_dir or os.getcwd()
        self._lock = asyncio.Lock()
        self._is_windows = sys.platform == "win32"

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        """确保 shell 进程存在且运行中。

        Windows 下启动 cmd.exe，Unix 下启动 bash。
        """
        if self._process is None or self._process.returncode is not None:
            if self._is_windows:
                self._process = await asyncio.create_subprocess_exec(
                    "cmd.exe",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self._working_dir,
                )
            else:
                self._process = await asyncio.create_subprocess_shell(
                    "/bin/bash --norc --noprofile",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self._working_dir,
                    env={**os.environ, "TERM": "dumb", "PS1": "", "PS2": ""},
                )
        return self._process

    async def execute(self, command: str, timeout: float = 120.0) -> CommandResult:
        """执行命令并返回结果

        Args:
            command: 要执行的 shell 命令
            timeout: 超时时间（秒），默认 120 秒

        Returns:
            CommandResult: 包含 stdout、exit_code、success、timed_out 的结果
        """
        async with self._lock:
            try:
                process = await self._ensure_process()
            except Exception as e:
                return CommandResult(
                    stdout=f"无法启动 shell 进程: {e}",
                    exit_code=-1,
                    success=False,
                    timed_out=False,
                )

            assert process.stdin is not None
            assert process.stdout is not None

            # 使用唯一哨兵标记区分命令输出边界
            sentinel = f"__LUMI_SENTINEL_{uuid.uuid4().hex[:12]}__"

            # 构造命令：执行用户命令，然后打印哨兵标记和退出码
            if self._is_windows:
                # cmd.exe: 用 %ERRORLEVEL% 获取退出码
                wrapped_command = (
                    f"{command}\r\necho.\r\necho {sentinel} %ERRORLEVEL%\r\n"
                )
            else:
                wrapped_command = (
                    f'{command}\n__lumi_ec=$?\necho ""\necho "{sentinel} $__lumi_ec"\n'
                )

            process.stdin.write(wrapped_command.encode())
            await process.stdin.drain()

            # 读取输出直到遇到哨兵标记
            output_lines: list[str] = []
            exit_code = -1
            timed_out = False

            try:
                while True:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(), timeout=timeout
                    )
                    if not line_bytes:
                        # 进程已终止
                        break

                    line = (
                        line_bytes.decode("utf-8", errors="replace")
                        .rstrip("\n")
                        .rstrip("\r")
                    )

                    if sentinel in line:
                        # 解析退出码
                        parts = line.split(sentinel)
                        if len(parts) >= 2:
                            code_str = parts[1].strip()
                            try:
                                exit_code = int(code_str)
                            except ValueError:
                                exit_code = -1
                        break
                    else:
                        output_lines.append(line)

            except asyncio.TimeoutError:
                timed_out = True
                # 超时后杀掉进程，下次调用会重新创建
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                self._process = None

            stdout = "\n".join(output_lines)
            return CommandResult(
                stdout=stdout,
                exit_code=exit_code,
                success=(exit_code == 0 and not timed_out),
                timed_out=timed_out,
            )

    async def close(self) -> None:
        """关闭 shell 会话"""
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.stdin.write(b"exit\n")
                await self._process.stdin.drain()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
            except (ProcessLookupError, BrokenPipeError, OSError):
                pass
            self._process = None


class SessionManager:
    """管理多个本地 shell 会话

    为不同的 thread_id 维护独立的持久化 shell 会话。
    """

    def __init__(self):
        self._sessions: dict[str, LocalShellSession] = {}

    def get_session(
        self, thread_id: str, working_dir: str | None = None
    ) -> LocalShellSession:
        """获取或创建持久化会话

        Args:
            thread_id: 线程标识符
            working_dir: 工作目录（仅在创建新会话时生效）

        Returns:
            LocalShellSession 实例
        """
        if thread_id not in self._sessions:
            self._sessions[thread_id] = LocalShellSession(working_dir)
            logger.debug(f"为线程 {thread_id} 创建新的 shell 会话")
        return self._sessions[thread_id]

    async def close_session(self, thread_id: str) -> None:
        """关闭指定线程的会话"""
        if thread_id in self._sessions:
            await self._sessions[thread_id].close()
            del self._sessions[thread_id]
            logger.debug(f"已关闭线程 {thread_id} 的 shell 会话")

    async def close_all(self) -> None:
        """关闭所有会话"""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        logger.debug("已关闭所有 shell 会话")


# 全局会话管理器单例
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """获取全局会话管理器单例"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager

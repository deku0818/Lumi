"""Shell 会话管理测试"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import pytest

from lumi.agents.runtime.shell_session import (
    LocalShellSession,
    ShellSessionManager,
    _BoundedOutputBuffer,
)

# Windows 下 shell 会话使用 cmd.exe，bash 语法不适用
_IS_WINDOWS = sys.platform == "win32"
_SKIP_WINDOWS = pytest.mark.skipif(_IS_WINDOWS, reason="bash-only test")


@pytest.fixture
async def shell_session(tmp_path):
    s = LocalShellSession(working_dir=str(tmp_path))
    yield s
    await s.close()


async def test_execute_simple_command(shell_session):
    result = await shell_session.execute("echo hello")
    assert result.success
    assert result.exit_code == 0
    assert "hello" in result.stdout


@_SKIP_WINDOWS
async def test_execute_failing_command(shell_session):
    result = await shell_session.execute("false")
    assert not result.success
    assert result.exit_code == 1


@_SKIP_WINDOWS
async def test_execute_preserves_env_state(shell_session):
    await shell_session.execute("export FOO=bar")
    result = await shell_session.execute("echo $FOO")
    assert result.success
    assert "bar" in result.stdout


@_SKIP_WINDOWS
async def test_execute_cd_persistence(shell_session, tmp_path):
    subdir = tmp_path / "mydir"
    subdir.mkdir()
    await shell_session.execute(f"cd {subdir}")
    result = await shell_session.execute("pwd")
    assert result.success
    assert str(subdir) in result.stdout


@_SKIP_WINDOWS
async def test_execute_timeout():
    s = LocalShellSession()
    try:
        result = await s.execute("sleep 999", timeout=0.5)
        assert result.timed_out
        assert not result.success
    finally:
        await s.close()


def _procs_with(marker: str) -> list[str]:
    """/proc 里命令行含 marker 的进程（不依赖 pgrep/ps）。"""
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if marker in args:
            found.append(args)
    return found


@_SKIP_WINDOWS
async def test_timeout_is_wall_clock_not_per_line(shell_session):
    # 回归：超时曾按「单行空闲」计时，持续有输出的命令永不超时、前台回合被无界挂死
    result = await asyncio.wait_for(
        shell_session.execute("while true; do echo x; sleep 0.1; done", timeout=1),
        timeout=10,
    )
    assert result.timed_out


@_SKIP_WINDOWS
async def test_timeout_kills_running_command(shell_session):
    # 回归：超时只杀掉 /bin/sh 包装层，bash 与正在跑的命令成为孤儿
    await shell_session.execute("sleep 313.7", timeout=0.5)
    await asyncio.sleep(0.5)
    assert _procs_with("sleep 313.7") == []


@_SKIP_WINDOWS
async def test_cancel_kills_command_and_next_call_is_clean(shell_session):
    # 回归：取消（stop / 切会话 / 断连）不终止 shell 里的命令，下一条命令排在它后面，
    # 还会读到它的输出和哨兵
    task = asyncio.create_task(shell_session.execute("sleep 3; echo stale", timeout=30))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    started = time.monotonic()
    result = await shell_session.execute("echo fresh", timeout=10)
    assert result.stdout.strip() == "fresh"
    assert time.monotonic() - started < 2


@_SKIP_WINDOWS
async def test_line_longer_than_stream_limit(shell_session):
    # 回归：单行超过 StreamReader 上限（64KB）时 readline 抛 ValueError，整条命令报内部错误
    result = await shell_session.execute(
        "head -c 200000 /dev/zero | tr '\\0' a; echo; echo after", timeout=10
    )
    assert result.success
    assert result.stdout.splitlines()[-1] == "after"
    followup = await shell_session.execute("echo ok", timeout=5)
    assert followup.stdout.strip() == "ok"


async def test_long_line_already_buffered_keeps_next_line():
    # 超长行连同换行已整段在缓冲里（读端落后于写端）时，下一行（可能就是哨兵）不能被吞
    reader = asyncio.StreamReader()
    reader.feed_data(b"a" * 70000 + b"\nafter\nSENTINEL 0\n")
    reader.feed_eof()
    buffer = _BoundedOutputBuffer(30 * 1024)
    exit_code = await asyncio.wait_for(
        LocalShellSession._collect_output(reader, "SENTINEL", buffer), timeout=5
    )
    assert exit_code == 0
    assert str(buffer).splitlines()[-1] == "after"


@_SKIP_WINDOWS
async def test_execute_truncates_oversized_output(shell_session):
    # yes | head 产生 40KB 输出，超过 30KB 阈值 → 触发截断 trailer
    result = await shell_session.execute("yes | head -n 20000", timeout=10.0)
    assert result.success
    assert result.stdout.startswith("y")
    assert "[output truncated" in result.stdout
    assert "KB dropped]" in result.stdout
    # 总大小不超过 30KB + trailer 少量开销（给 2KB 余量）
    assert len(result.stdout.encode()) <= 30 * 1024 + 2048


@_SKIP_WINDOWS
async def test_execute_small_output_no_trailer(shell_session):
    result = await shell_session.execute("echo hello")
    assert result.success
    assert "hello" in result.stdout
    assert "[output truncated" not in result.stdout


@_SKIP_WINDOWS
async def test_execute_truncates_multibyte_utf8(shell_session):
    # 每个 "中" UTF-8 占 3 字节；20000 行 ≈ 80KB → 必然截断
    # 防回归：确保字节会计用 encode() 而非 len(str)
    result = await shell_session.execute("yes 中文 | head -n 20000", timeout=10.0)
    assert result.success
    assert "[output truncated" in result.stdout
    # 实际字节数应受 30KB 上限约束（trailer 约 30 字节）
    assert len(result.stdout.encode()) <= 30 * 1024 + 2048


async def test_session_close():
    s = LocalShellSession()
    await s.execute("echo init")
    assert s._process is not None
    await s.close()
    assert s._process is None


async def test_session_manager_get_and_reuse():
    mgr = ShellSessionManager()
    s1 = mgr.get_session("thread-a", working_dir=tempfile.gettempdir())
    s2 = mgr.get_session("thread-a")
    assert s1 is s2
    await mgr.close_all()


async def test_session_manager_close_all():
    mgr = ShellSessionManager()
    s1 = mgr.get_session("t1")
    s2 = mgr.get_session("t2")
    await s1.execute("echo a")
    await s2.execute("echo b")
    await mgr.close_all()
    assert len(mgr._sessions) == 0

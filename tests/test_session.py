"""Shell 会话管理测试"""

import sys
import tempfile

import pytest

from lumi.agents.runtime.session import (
    LocalShellSession,
    SessionManager,
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
    mgr = SessionManager()
    s1 = mgr.get_session("thread-a", working_dir=tempfile.gettempdir())
    s2 = mgr.get_session("thread-a")
    assert s1 is s2
    await mgr.close_all()


async def test_session_manager_close_all():
    mgr = SessionManager()
    s1 = mgr.get_session("t1")
    s2 = mgr.get_session("t2")
    await s1.execute("echo a")
    await s2.execute("echo b")
    await mgr.close_all()
    assert len(mgr._sessions) == 0

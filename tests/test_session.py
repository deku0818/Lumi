"""Shell 会话管理测试"""

import pytest

from lumi.agents.tools.session import (
    LocalShellSession,
    SessionManager,
)


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


async def test_execute_failing_command(shell_session):
    result = await shell_session.execute("false")
    assert not result.success
    assert result.exit_code == 1


async def test_execute_preserves_env_state(shell_session):
    await shell_session.execute("export FOO=bar")
    result = await shell_session.execute("echo $FOO")
    assert result.success
    assert "bar" in result.stdout


async def test_execute_cd_persistence(shell_session, tmp_path):
    subdir = tmp_path / "mydir"
    subdir.mkdir()
    await shell_session.execute(f"cd {subdir}")
    result = await shell_session.execute("pwd")
    assert result.success
    assert str(subdir) in result.stdout


async def test_execute_timeout():
    s = LocalShellSession()
    try:
        result = await s.execute("sleep 999", timeout=0.5)
        assert result.timed_out
        assert not result.success
    finally:
        await s.close()


async def test_session_close():
    s = LocalShellSession()
    await s.execute("echo init")
    assert s._process is not None
    await s.close()
    assert s._process is None


async def test_session_manager_get_and_reuse():
    mgr = SessionManager()
    s1 = mgr.get_session("thread-a", working_dir="/tmp")
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

"""Bash 工具测试"""

from lumi.agents.runtime.shell_session import CommandResult
from lumi.agents.tools.providers.bash import _format_result


class TestFormatResult:
    def test_success(self):
        r = CommandResult(stdout="hello", exit_code=0, success=True, timed_out=False)
        assert _format_result(r) == "hello"

    def test_no_output(self):
        r = CommandResult(stdout="", exit_code=0, success=True, timed_out=False)
        assert _format_result(r) == "<no output>"

    def test_timeout(self):
        r = CommandResult(stdout="", exit_code=-1, success=False, timed_out=True)
        assert _format_result(r) == "Error: Timeout"

    def test_failure(self):
        r = CommandResult(stdout="oops", exit_code=2, success=False, timed_out=False)
        result = _format_result(r)
        assert "Exit code 2" in result
        assert "oops" in result

    def test_failure_no_stdout(self):
        r = CommandResult(stdout="", exit_code=1, success=False, timed_out=False)
        assert _format_result(r) == "Error: Exit code 1"


async def test_bash_tool_executes_command(authorized_tmp_dir):
    from lumi.agents.tools.providers.bash import bash

    result = await bash.ainvoke(
        {"command": "echo integration_test", "description": "集成测试"}
    )
    assert "integration_test" in result


async def test_background_rejects_shell_ampersand(authorized_tmp_dir):
    """后台任务命令自带 & 时不执行、直接报错（守卫在 start_task 之前，不会真起任务）。"""
    from lumi.agents.tools.providers.bash import bash

    result = await bash.ainvoke(
        {
            "command": 'sleep 5 2>&1 &\necho "PID: $!"',
            "description": "双后台机制叠加",
            "run_in_background": True,
        }
    )
    assert result.startswith("Error:")
    assert "&" in result


async def test_shell_sessions_isolated_per_thread():
    """ShellSessionManager 按 thread_id 维护独立持久 shell；close_session 关闭后惰性重建。"""
    from lumi.agents.runtime.shell_session import get_shell_session_manager

    mgr = get_shell_session_manager()
    sa = mgr.get_session("thread-A", working_dir="/tmp")
    sb = mgr.get_session("thread-B", working_dir="/tmp")
    assert sa is not sb  # 不同会话各自独立的 shell（cwd/env 互不串）
    assert mgr.get_session("thread-A") is sa  # 同 thread 复用同一 shell

    await mgr.close_session("thread-A")
    assert mgr.get_session("thread-A") is not sa  # 关闭后惰性重建为新实例
    assert mgr.get_session("thread-B") is sb  # 只关指定 thread，不影响其它会话


async def test_bash_keys_shell_by_current_thread(monkeypatch, authorized_tmp_dir):
    """回归（review #3）：bash 按 current_thread_id 取 shell，而非全局共用 "default"。

    并发会话共用 "default" 时，一个会话的 cd 会污染另一个、相对路径跑错项目。
    """
    from lumi.agents.runtime.bg_tasks import current_thread_id
    from lumi.agents.tools.providers import bash as bash_mod

    seen_thread_ids: list[str] = []

    class _FakeSession:
        async def execute(self, command, timeout=120.0):
            return CommandResult(
                stdout="ok", exit_code=0, success=True, timed_out=False
            )

    class _FakeMgr:
        def get_session(self, thread_id, working_dir=None):
            seen_thread_ids.append(thread_id)
            return _FakeSession()

    monkeypatch.setattr(bash_mod, "get_shell_session_manager", lambda: _FakeMgr())

    current_thread_id.set("sess-A")
    await bash_mod.bash.ainvoke({"command": "echo hi", "description": "d"})
    current_thread_id.set("sess-B")
    await bash_mod.bash.ainvoke({"command": "echo hi", "description": "d"})

    assert seen_thread_ids == ["sess-A", "sess-B"]  # 各会话用各自 thread 的 shell


async def test_subagent_shell_isolated_and_reaped():
    """子代理独立 shell：作用域内用专属 key、退出即回收、外层 key 不受影响（cd 不外溢）。"""
    from lumi.agents.runtime.shell_session import (
        current_shell_key,
        get_shell_session_manager,
        run_with_shell,
    )

    mgr = get_shell_session_manager()
    seen: dict[str, str] = {}

    async def body() -> str:
        seen["key"] = current_shell_key()
        mgr.get_session(current_shell_key(), working_dir="/tmp")  # 模拟 bash 建 shell
        return "ok"

    assert current_shell_key() == ""  # 父上下文无专属 key
    r = await run_with_shell("sub-xyz", body())
    assert r == "ok"
    assert seen["key"] == "sub-xyz"  # 作用域内用专属 key
    assert "sub-xyz" not in mgr._sessions  # 退出即回收，不泄漏
    assert current_shell_key() == ""  # 外层 key 不受影响（隔离）


async def test_subagent_shells_isolated_between_siblings():
    """并发兄弟子代理各用各的 shell key，交错执行也不互相串（copy_context 隔离）。"""
    import asyncio as _aio

    from lumi.agents.runtime.shell_session import current_shell_key, run_with_shell

    seen: dict[str, str] = {}

    async def body(tag: str) -> str:
        seen[tag] = current_shell_key()
        await _aio.sleep(0.01)  # 让兄弟交错
        seen[tag + "_after"] = current_shell_key()
        return tag

    await _aio.gather(
        run_with_shell("sub-A", body("A")),
        run_with_shell("sub-B", body("B")),
    )
    assert seen["A"] == "sub-A" and seen["A_after"] == "sub-A"  # 交错后仍是自己的 key
    assert seen["B"] == "sub-B" and seen["B_after"] == "sub-B"

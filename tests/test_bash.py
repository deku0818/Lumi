"""Bash 工具测试"""

from lumi.agents.tools.providers.bash import _format_result
from lumi.agents.runtime.session import CommandResult


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

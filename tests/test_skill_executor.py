"""嵌入式命令执行测试"""

import pytest

from lumi.agents.tools.providers.skill_executor import SkillCommandExecutor


@pytest.fixture
def executor(tmp_path):
    return SkillCommandExecutor(
        working_dir=str(tmp_path),
        skill_name="test-skill",
        timeout=5.0,
    )


def test_has_commands_inline():
    assert SkillCommandExecutor.has_commands("Hello !`echo hi` world")


def test_has_commands_multiline():
    assert SkillCommandExecutor.has_commands("Hello !```echo hi``` world")


def test_has_commands_none():
    assert not SkillCommandExecutor.has_commands("No commands here")


async def test_execute_commands_replacement(executor):
    content = "Result: !`echo hello`"
    result = await executor.execute_commands(content)
    assert "hello" in result
    assert "!`echo hello`" not in result


async def test_execute_commands_failure_preserves(executor):
    content = "Keep: !`false`"
    result = await executor.execute_commands(content)
    assert "!`false`" in result


async def test_execute_commands_timeout(tmp_path):
    executor = SkillCommandExecutor(
        working_dir=str(tmp_path),
        skill_name="test",
        timeout=0.5,
    )
    content = "Wait: !`sleep 999`"
    result = await executor.execute_commands(content)
    # 超时时保留原文
    assert "!`sleep 999`" in result


async def test_execute_commands_multiple(executor):
    content = "A: !`echo aaa` B: !`echo bbb`"
    result = await executor.execute_commands(content)
    assert "aaa" in result
    assert "bbb" in result

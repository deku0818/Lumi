"""background_task 管理工具测试"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lumi.agents.tools.providers.background_task import (
    _format_task_list,
    _format_task_status,
    _handle_stop,
)
from lumi.agents.tools.task_registry import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    get_task_registry,
)


@pytest.fixture
def registry():
    return get_task_registry()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty(registry):
    result = _format_task_list()
    assert "没有后台任务" in result


def test_list_with_tasks(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_aaa",
            kind=TaskKind.BASH,
            status=TaskStatus.RUNNING,
            label="echo test",
            started_at=time.time() - 10,
            output_file=Path("/tmp/bg_aaa.txt"),
        )
    )
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_bbb",
            kind=TaskKind.AGENT,
            status=TaskStatus.COMPLETED,
            label="agent:runner",
            started_at=time.time() - 5,
            completed_at=time.time(),
            output_file=Path("/tmp/bg_bbb.txt"),
            agent_name="runner",
        )
    )
    result = _format_task_list()
    assert "bg_aaa" in result
    assert "bg_bbb" in result
    assert "bash" in result
    assert "agent" in result


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_nonexistent(registry):
    result = _format_task_status("bg_nonexistent")
    assert "不存在" in result


def test_status_running_bash(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_ccc",
            kind=TaskKind.BASH,
            status=TaskStatus.RUNNING,
            label="make build",
            started_at=time.time() - 3,
            output_file=Path("/tmp/bg_ccc.txt"),
        )
    )
    result = _format_task_status("bg_ccc")
    assert "bg_ccc" in result
    assert "bash" in result
    assert "running" in result
    assert "stop" in result


def test_status_completed_agent(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_ddd",
            kind=TaskKind.AGENT,
            status=TaskStatus.COMPLETED,
            label="agent:test",
            started_at=time.time() - 10,
            completed_at=time.time(),
            output_file=Path("/tmp/bg_ddd.txt"),
            agent_name="test",
        )
    )
    result = _format_task_status("bg_ddd")
    assert "Agent: test" in result
    assert "Read" in result


def test_status_failed_with_error(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_eee",
            kind=TaskKind.BASH,
            status=TaskStatus.FAILED,
            label="bad_cmd",
            started_at=time.time(),
            completed_at=time.time(),
            output_file=Path("/tmp/bg_eee.txt"),
            exit_code=1,
            error="进程退出码: 1",
        )
    )
    result = _format_task_status("bg_eee")
    assert "Exit Code: 1" in result
    assert "Error:" in result


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


async def test_stop_nonexistent(registry):
    result = await _handle_stop("bg_nonexistent")
    assert "不存在" in result


async def test_stop_already_completed(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_fff",
            kind=TaskKind.BASH,
            status=TaskStatus.COMPLETED,
            label="echo done",
            started_at=time.time(),
            output_file=Path("/tmp/bg_fff.txt"),
        )
    )
    result = await _handle_stop("bg_fff")
    assert "无法停止" in result


async def test_stop_agent_task(registry):
    async def dummy():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy())
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_ggg",
            kind=TaskKind.AGENT,
            status=TaskStatus.RUNNING,
            label="agent:slow",
            started_at=time.time(),
            output_file=Path("/tmp/bg_ggg.txt"),
            agent_name="slow",
            async_task=task,
        )
    )
    result = await _handle_stop("bg_ggg")
    assert "已停止" in result
    assert task.cancelling()


async def test_stop_bash_task(registry):
    registry.register(
        BackgroundTaskEntry(
            task_id="bg_hhh",
            kind=TaskKind.BASH,
            status=TaskStatus.RUNNING,
            label="sleep 999",
            started_at=time.time(),
            output_file=Path("/tmp/bg_hhh.txt"),
        )
    )
    import lumi.agents.tools.session as session_mod

    mock_mgr = AsyncMock()
    mock_mgr.cancel_task = AsyncMock()
    mock_sm = AsyncMock()
    mock_sm.has_bg_manager = True
    mock_sm.bg_manager = mock_mgr
    session_mod._session_manager = mock_sm
    try:
        result = await _handle_stop("bg_hhh")
        mock_mgr.cancel_task.assert_awaited_once_with("bg_hhh")
        assert "已停止" in result
    finally:
        session_mod._session_manager = None

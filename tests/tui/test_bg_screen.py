"""后台任务面板测试"""

from __future__ import annotations

import time
from pathlib import Path

from lumi.agents.tools.task_registry import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
)
from lumi.tui.screens.bg_screen import (
    _format_duration,
    _status_icon,
)


# ---------------------------------------------------------------------------
# _status_icon
# ---------------------------------------------------------------------------


def test_status_icon_running():
    icon, style = _status_icon(TaskStatus.RUNNING)
    assert icon == "●"
    assert "cyan" in style


def test_status_icon_completed():
    icon, style = _status_icon(TaskStatus.COMPLETED)
    assert icon == "✓"
    assert "green" in style


def test_status_icon_failed():
    icon, style = _status_icon(TaskStatus.FAILED)
    assert icon == "✗"
    assert "red" in style


def test_status_icon_timed_out():
    icon, style = _status_icon(TaskStatus.TIMED_OUT)
    assert icon == "⏱"
    assert "yellow" in style


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


def test_format_duration_seconds():
    now = time.time()
    assert _format_duration(now - 30) == "30s"


def test_format_duration_minutes():
    now = time.time()
    assert _format_duration(now - 125) == "2m5s"


def test_format_duration_hours():
    now = time.time()
    assert _format_duration(now - 3661) == "1h1m"


def test_format_duration_with_completed_at():
    started = 1000.0
    completed = 1045.0
    assert _format_duration(started, completed) == "45s"


# ---------------------------------------------------------------------------
# BackgroundTaskEntry.prompt
# ---------------------------------------------------------------------------


def test_entry_prompt_default():
    entry = BackgroundTaskEntry(
        task_id="bg_test",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="echo hi",
        started_at=time.time(),
        output_file=Path("/tmp/test.txt"),
    )
    assert entry.prompt == ""


def test_entry_prompt_agent():
    entry = BackgroundTaskEntry(
        task_id="bg_agent",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="agent:test-runner",
        started_at=time.time(),
        output_file=Path("/tmp/agent.txt"),
        agent_name="test-runner",
        prompt="Review the test coverage for this PR",
    )
    assert entry.prompt == "Review the test coverage for this PR"

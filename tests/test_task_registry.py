"""TaskRegistry 统一后台任务注册中心测试"""

import asyncio
import time
from pathlib import Path

from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    NotificationQueue,
    TaskKind,
    TaskRegistry,
    TaskStatus,
    format_notification,
    get_task_registry,
)

# ---------------------------------------------------------------------------
# NotificationQueue
# ---------------------------------------------------------------------------


def test_notification_queue_enqueue_and_drain():
    q = NotificationQueue()
    q.enqueue("<xml>a</xml>")
    q.enqueue("<xml>b</xml>")
    items = q.drain_all()
    assert items == ["<xml>a</xml>", "<xml>b</xml>"]
    assert q.is_empty()


def test_notification_queue_drain_empty():
    q = NotificationQueue()
    assert q.drain_all() == []


# ---------------------------------------------------------------------------
# format_notification
# ---------------------------------------------------------------------------


def test_format_notification_bash_completed():
    entry = BackgroundTaskEntry(
        task_id="bg_abc",
        kind=TaskKind.BASH,
        status=TaskStatus.COMPLETED,
        label="echo hello",
        started_at=time.time(),
        output_file=Path("/tmp/bg_abc.txt"),
        exit_code=0,
    )
    xml = format_notification(entry)
    assert "<task-kind>bash</task-kind>" in xml
    assert "<status>completed</status>" in xml
    assert '命令 "echo hello" 已完成' in xml


def test_format_notification_bash_timed_out():
    entry = BackgroundTaskEntry(
        task_id="bg_abc",
        kind=TaskKind.BASH,
        status=TaskStatus.TIMED_OUT,
        label="sleep 999",
        started_at=time.time(),
        output_file=Path("/tmp/bg_abc.txt"),
    )
    xml = format_notification(entry)
    assert "超时" in xml


def test_format_notification_agent_completed():
    entry = BackgroundTaskEntry(
        task_id="bg_xyz",
        kind=TaskKind.AGENT,
        status=TaskStatus.COMPLETED,
        label="agent:test-runner",
        started_at=time.time(),
        output_file=Path("/tmp/bg_xyz.txt"),
        agent_name="test-runner",
    )
    xml = format_notification(entry)
    assert "<task-kind>agent</task-kind>" in xml
    assert '代理 "test-runner" 已完成' in xml


def test_format_notification_agent_failed():
    entry = BackgroundTaskEntry(
        task_id="bg_xyz",
        kind=TaskKind.AGENT,
        status=TaskStatus.FAILED,
        label="agent:test-runner",
        started_at=time.time(),
        output_file=Path("/tmp/bg_xyz.txt"),
        agent_name="test-runner",
        error="LLM timeout",
    )
    xml = format_notification(entry)
    assert "失败" in xml
    assert "LLM timeout" in xml


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_001",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="ls -la",
        started_at=time.time(),
        output_file=Path("/tmp/bg_001.txt"),
    )
    reg.register(entry)
    assert reg.get("bg_001") is entry
    assert reg.get("nonexistent") is None


def test_registry_all_tasks_sorted():
    reg = TaskRegistry()
    now = time.time()
    e1 = BackgroundTaskEntry(
        task_id="bg_a",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="a",
        started_at=now + 1,
        output_file=Path("/tmp/a.txt"),
    )
    e2 = BackgroundTaskEntry(
        task_id="bg_b",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="b",
        started_at=now,
        output_file=Path("/tmp/b.txt"),
    )
    reg.register(e1)
    reg.register(e2)
    tasks = reg.all_tasks()
    assert tasks[0].task_id == "bg_b"
    assert tasks[1].task_id == "bg_a"


def test_registry_update_status():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_002",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="make",
        started_at=time.time(),
        output_file=Path("/tmp/bg_002.txt"),
    )
    reg.register(entry)
    reg.update_status("bg_002", TaskStatus.COMPLETED, exit_code=0)
    assert entry.status == TaskStatus.COMPLETED
    assert entry.exit_code == 0
    assert entry.completed_at is not None


def test_registry_update_status_nonexistent():
    reg = TaskRegistry()
    reg.update_status("nonexistent", TaskStatus.FAILED)


def test_registry_enqueue_notification():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_003",
        kind=TaskKind.AGENT,
        status=TaskStatus.COMPLETED,
        label="agent:foo",
        started_at=time.time(),
        output_file=Path("/tmp/bg_003.txt"),
        agent_name="foo",
    )
    reg.register(entry)
    reg.enqueue_notification("bg_003")
    items = reg.notification_queue.drain_all()
    assert len(items) == 1
    assert "bg_003" in items[0]


async def test_registry_cancel_agent_task():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_004",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="agent:slow",
        started_at=time.time(),
        output_file=Path("/tmp/bg_004.txt"),
        agent_name="slow",
    )

    async def dummy():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy())
    entry.async_task = task

    reg.register(entry)
    assert reg.cancel_agent_task("bg_004")
    # cancel_agent_task 只请求取消，不改 status
    assert task.cancelling()


def test_registry_cancel_agent_task_not_running():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_005",
        kind=TaskKind.AGENT,
        status=TaskStatus.COMPLETED,
        label="agent:done",
        started_at=time.time(),
        output_file=Path("/tmp/bg_005.txt"),
    )
    reg.register(entry)
    assert not reg.cancel_agent_task("bg_005")


async def test_registry_cleanup():
    reg = TaskRegistry()

    async def dummy():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy())

    entry = BackgroundTaskEntry(
        task_id="bg_006",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="agent:x",
        started_at=time.time(),
        output_file=Path("/tmp/bg_006.txt"),
        async_task=task,
    )
    reg.register(entry)
    reg.cleanup()
    assert task.cancelling()
    assert reg.get("bg_006") is None


def test_get_task_registry_singleton():
    r1 = get_task_registry()
    r2 = get_task_registry()
    assert r1 is r2


def test_entry_async_task_defaults_to_none():
    entry = BackgroundTaskEntry(
        task_id="bg_test",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="test",
        started_at=time.time(),
        output_file=Path("/tmp/test.txt"),
    )
    assert entry.async_task is None


def test_registry_register_duplicate_raises():
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_dup",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="dup",
        started_at=time.time(),
        output_file=Path("/tmp/dup.txt"),
    )
    reg.register(entry)
    import pytest

    with pytest.raises(ValueError, match="重复"):
        reg.register(entry)


def test_registry_update_status_terminal_guard():
    """终态不可被覆盖。"""
    reg = TaskRegistry()
    entry = BackgroundTaskEntry(
        task_id="bg_term",
        kind=TaskKind.BASH,
        status=TaskStatus.RUNNING,
        label="term",
        started_at=time.time(),
        output_file=Path("/tmp/term.txt"),
    )
    reg.register(entry)
    reg.update_status("bg_term", TaskStatus.COMPLETED, exit_code=0)
    assert entry.status == TaskStatus.COMPLETED
    # 再次更新应被忽略
    reg.update_status("bg_term", TaskStatus.FAILED, error="should be ignored")
    assert entry.status == TaskStatus.COMPLETED
    assert entry.error is None


async def test_registry_cleanup_enqueues_notifications():
    """cleanup 应在清空 entries 前发送通知。"""
    reg = TaskRegistry()

    async def dummy():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy())
    entry = BackgroundTaskEntry(
        task_id="bg_cleanup",
        kind=TaskKind.AGENT,
        status=TaskStatus.RUNNING,
        label="agent:cleanup",
        started_at=time.time(),
        output_file=Path("/tmp/cleanup.txt"),
        async_task=task,
    )
    reg.register(entry)
    reg.cleanup()
    notifications = reg.notification_queue.drain_all()
    assert len(notifications) == 1
    assert "bg_cleanup" in notifications[0]

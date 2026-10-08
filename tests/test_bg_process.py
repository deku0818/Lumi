"""后台 Bash 进程：按组终止与句柄回收。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from lumi.agents.runtime import bg_process
from lumi.agents.runtime.bg_process import BackgroundTaskManager
from lumi.agents.runtime.bg_tasks import TaskStatus, get_task_registry

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="进程组语义")


def _alive(marker: str) -> bool:
    """/proc 里是否还有命令行含 marker 的进程。"""
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if marker in cmdline.read_bytes().replace(b"\0", b" ").decode():
                return True
        except OSError:
            continue
    return False


async def _wait_done(task_id: str) -> None:
    while get_task_registry().get(task_id).status == TaskStatus.RUNNING:
        await asyncio.sleep(0.05)


async def test_timeout_kills_child_that_ignores_sigterm(isolated_config, monkeypatch):
    # 回归：wrapper sh 收到 SIGTERM 立即退出，wait 马上返回、SIGKILL 被跳过，
    # 忽略 SIGTERM 的子进程存活成孤儿
    monkeypatch.setattr(bg_process, "GRACEFUL_SHUTDOWN_TIMEOUT", 0.5)
    code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(313.83)"
    entry = await BackgroundTaskManager().start_task(
        f'python3 -c "{code}"; echo done', 0.5, str(isolated_config)
    )
    await asyncio.wait_for(_wait_done(entry.task_id), 10)
    assert entry.status == TaskStatus.TIMED_OUT
    await asyncio.sleep(0.2)
    assert not _alive("313.83")


async def test_finished_task_releases_handle(isolated_config):
    # 回归：句柄与监控协程只在 cleanup_all 里清，任务自然结束后永不摘除
    mgr = BackgroundTaskManager()
    entry = await mgr.start_task("true", None, str(isolated_config))
    await asyncio.wait_for(_wait_done(entry.task_id), 10)
    await asyncio.sleep(0.05)
    assert entry.task_id not in mgr._handles
    assert entry.task_id not in mgr._monitors

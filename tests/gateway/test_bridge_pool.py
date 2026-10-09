"""渠道会话池回收：不在轮仍在用 bridge 时关它，也不按忙会话数串行干等。"""

from __future__ import annotations

import asyncio
from time import monotonic

from lumi.gateway.channels.feishu.bridge_pool import BridgePool
from lumi.sessions.thread_runs import ThreadRunLock


class _Bridge:
    def __init__(self, closed: list) -> None:
        self._closed = closed

    def reject_pending(self) -> int:
        return 0

    async def close(self) -> None:
        self._closed.append(self)


async def test_close_all_cancels_turns_before_closing():
    # 回归：逐会话串行等锁 5s、不取消在途轮，超时后在轮脚下关库（use-after-close）
    pool = BridgePool()
    closed: list = []
    turns: list[asyncio.Task] = []
    locks = []
    for tid in ("a", "b"):
        lock = ThreadRunLock()
        locks.append(lock)
        pool._bridges[tid] = _Bridge(closed)
        pool._locks[tid] = lock

        async def turn(lock: ThreadRunLock = lock) -> None:
            async with lock:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.05)  # 取消收尾（写回半截回复）
                    raise

        task = asyncio.create_task(turn())
        pool.run_tasks[tid] = task
        turns.append(task)
    await asyncio.sleep(0)

    started = monotonic()
    await pool.close_all()
    assert monotonic() - started < 1
    assert all(t.done() for t in turns)
    assert len(closed) == 2
    assert all(not lock.locked() for lock in locks)


async def test_cancelled_pool_cleanup_releases_shared_locks():
    pool = BridgePool()
    first, second = ThreadRunLock(), ThreadRunLock()
    pool._locks.update(first=first, second=second)
    pool._bridges.update(first=_Bridge([]), second=_Bridge([]))
    async with second:
        closing = asyncio.create_task(pool.close_all())

        async def wait_for_first():
            while not first.locked():
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_first(), 1)
        closing.cancel()
        try:
            await closing
        except asyncio.CancelledError:
            pass
    assert not first.locked()
    assert not second.locked()
    async with first.cancel_and_hold():
        pass

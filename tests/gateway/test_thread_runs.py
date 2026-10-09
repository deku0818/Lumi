"""跨入口的 thread 互斥、删除屏障与长期轮询取消隔离。"""

import asyncio

import pytest

from lumi.sessions.thread_runs import (
    ThreadDeletingError,
    ThreadRunRegistry,
    preserve_poller,
)


async def test_queued_handoff_stays_busy_for_pollers():
    lock = ThreadRunRegistry().lock_for("t")
    await lock.acquire()

    async def queued():
        async with lock:
            pass

    task = asyncio.create_task(queued())
    await asyncio.sleep(0)
    lock.release()
    assert lock.locked()  # 已释放，但下一轮尚未被事件循环唤醒。
    await task
    assert not lock.locked()


async def test_delete_cancels_owner_and_waiter_and_waits_for_writeback():
    registry = ThreadRunRegistry()
    lock = registry.lock_for("t")
    assert registry.lock_for("t") is lock
    started = asyncio.Event()
    finalizing = asyncio.Event()
    finish = asyncio.Event()
    deleted = asyncio.Event()
    queued_ran = False

    async def owner():
        async with lock:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalizing.set()
                await finish.wait()

    async def waiter():
        nonlocal queued_ran
        async with lock:
            queued_ran = True

    async def delete():
        async with lock.cancel_and_hold():
            deleted.set()
            with pytest.raises(ThreadDeletingError):
                async with lock:
                    pass

    active = asyncio.create_task(owner())
    await started.wait()
    queued = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    deletion = asyncio.create_task(delete())
    await finalizing.wait()
    assert not deleted.is_set()
    # 另一 thread 不受删除屏障影响。
    async with registry.lock_for("other"):
        pass
    finish.set()
    await asyncio.wait_for(deletion, 1)
    results = await asyncio.gather(active, queued, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    assert not queued_ran
    assert not lock.locked()
    async with lock:
        pass  # 删除后明确的新轮仍可使用会话。


async def test_delete_does_not_cancel_writeback_twice():
    lock = ThreadRunRegistry().lock_for("t")
    started = asyncio.Event()
    finalizing = asyncio.Event()
    finish = asyncio.Event()
    written = False

    async def owner():
        nonlocal written
        async with lock:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalizing.set()
                await finish.wait()
                written = True

    async def delete():
        async with lock.cancel_and_hold():
            assert written

    active = asyncio.create_task(owner())
    await started.wait()
    active.cancel()  # 用户已点 stop，写回正在进行。
    await finalizing.wait()
    deletion = asyncio.create_task(delete())
    await asyncio.sleep(0)
    finish.set()
    await asyncio.wait_for(deletion, 1)
    await asyncio.gather(active, return_exceptions=True)
    assert written


async def test_delete_keeps_poller_alive_but_shutdown_cancels_it():
    lock = ThreadRunRegistry().lock_for("t")
    started = asyncio.Event()
    continued = asyncio.Event()

    async def poller():
        async with preserve_poller(lock):
            started.set()
            await asyncio.Event().wait()
        continued.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(poller())
    await started.wait()
    async with lock.cancel_and_hold():
        pass
    await asyncio.wait_for(continued.wait(), 1)
    assert not task.done()
    assert not task.cancelling()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_shutdown_during_delete_cleanup_is_not_swallowed():
    lock = ThreadRunRegistry().lock_for("t")
    started = asyncio.Event()
    finalizing = asyncio.Event()

    async def poller():
        async with preserve_poller(lock):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalizing.set()
                await asyncio.Event().wait()

    async def delete():
        async with lock.cancel_and_hold():
            pass

    task = asyncio.create_task(poller())
    await started.wait()
    deletion = asyncio.create_task(delete())
    await finalizing.wait()
    task.cancel()  # 停机覆盖正在处理的删除取消。
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(deletion, 1)
    assert not lock.locked()


async def test_concurrent_deletions_wait_for_each_other():
    lock = ThreadRunRegistry().lock_for("t")
    started = asyncio.Event()
    finish = asyncio.Event()
    order = []

    async def first():
        async with lock.cancel_and_hold():
            order.append("first")
            started.set()
            await finish.wait()

    async def second():
        async with lock.cancel_and_hold():
            order.append("second")

    a = asyncio.create_task(first())
    await started.wait()
    b = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert order == ["first"]
    finish.set()
    await asyncio.wait_for(asyncio.gather(a, b), 1)
    assert order == ["first", "second"]


async def test_cancelled_delete_leaves_lock_reusable():
    lock = ThreadRunRegistry().lock_for("t")
    entered = asyncio.Event()

    async def delete():
        async with lock.cancel_and_hold():
            entered.set()
            await asyncio.Event().wait()

    deletion = asyncio.create_task(delete())
    await entered.wait()
    deletion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deletion
    assert not lock.deleting
    async with lock:
        pass

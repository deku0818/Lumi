"""进程内按 thread 串行化运行与删除，desktop / IM / cron 共用的会话基础设施。

连接自己的锁保护 bridge 的可变指向；这里的锁保护线程 checkpoint。登记覆盖等待者，
删除先封住新运行、取消已有运行，待所有持锁区（含取消写回）退出后再独占删除。
弱引用仅回收无人使用的锁；持有者、等待者与会话池始终保有强引用。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

_DELETE_CANCELLED = object()


class ThreadDeletingError(ValueError):
    """线程正在删除，本次运行未开始。"""


class ThreadRunLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._delete_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._drained = asyncio.Event()
        self._drained.set()
        self._owner: asyncio.Task | None = None
        self.deleting = False
        self.revision = 0

    def locked(self) -> bool:
        # release 与排队者真正接手之间，asyncio.Lock.locked() 短暂为 False。
        # 仍视作忙，避免长期 poller 插入等待队列（其取消只能发生在轮内）。
        return self.deleting or self._lock.locked() or bool(self._tasks)

    async def acquire(self) -> bool:
        if self.deleting:
            raise ThreadDeletingError("会话正在删除，请稍后再试")
        task = asyncio.current_task()
        if task in self._tasks:
            raise RuntimeError("不能重复获取同一会话的运行锁")
        self._tasks.add(task)
        self._drained.clear()
        try:
            await self._lock.acquire()
        except BaseException:
            self._finish(task)
            raise
        self._owner = task
        return True

    def _finish(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not self._tasks:
            self._drained.set()

    def release(self) -> None:
        owner = self._owner
        self._owner = None
        self._lock.release()
        self._finish(owner)

    async def __aenter__(self) -> ThreadRunLock:
        await self.acquire()
        return self

    async def __aexit__(self, *_args) -> None:
        self.release()

    @asynccontextmanager
    async def idle(self):
        """生命周期清理只等待运行退出，不登记为可取消的 agent 运行。"""
        async with self._lock:
            yield

    @asynccontextmanager
    async def cancel_and_hold(self):
        """取消当前持锁者和排队者，等取消收尾完成后独占线程。并发删除依次执行。"""
        if asyncio.current_task() in self._tasks:
            raise RuntimeError("不能在持有运行锁时删除同一会话")
        async with self._delete_lock:
            self.deleting = True
            self.revision += 1
            try:
                for task in tuple(self._tasks):
                    # 二次取消会打断 finalize 中的写回；已取消的只等收尾。
                    if not task.done() and not task.cancelling():
                        task.cancel(_DELETE_CANCELLED)
                await self._drained.wait()
                async with self._lock:
                    yield
            finally:
                self.deleting = False


@asynccontextmanager
async def preserve_poller(lock):
    """调用方已判空闲、无 await 地进入；吸收删除取消，停机/重载取消仍传播。"""
    async with lock:
        try:
            yield
        except asyncio.CancelledError as exc:
            task = asyncio.current_task()
            if exc.args != (_DELETE_CANCELLED,) or task.cancelling() != 1:
                raise
            task.uncancel()


class ThreadRunRegistry:
    def __init__(self) -> None:
        self._locks: WeakValueDictionary[str, ThreadRunLock] = WeakValueDictionary()

    def lock_for(self, thread_id: str) -> ThreadRunLock:
        lock = self._locks.get(thread_id)
        if lock is None:
            lock = ThreadRunLock()
            self._locks[thread_id] = lock
        return lock

    def is_deleting(self, thread_id: str) -> bool:
        lock = self._locks.get(thread_id)
        return lock is not None and lock.deleting


thread_runs = ThreadRunRegistry()

"""按 thread 的只读事件观测 pub/sub（cron 执行直播用）。

cron 运行在调度器里、没有自己的 WS 连接；桌面打开一条正在运行的 cron 线程时登记为
该 thread 的观测者，运行产出的事件流经 :meth:`ThreadObserverHub.publish` 扇给观测者。

**核心不变量**：观测者绝不背压 run。每观测者一条有界队列，满即丢最旧一条（观测者的
实时视图短暂落后，run 完成后 loadHistory 从 checkpoint 重对齐），发布侧永不 await 慢
连接。多观测者各自独立队列。
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

# 每观测者队列上限：token 级 delta 高频，256 帧足够缓冲一小段网络抖动；
# 满了丢最旧、保持流向前推进（丢失的增量由完成后的 loadHistory 修正）。
_QUEUE_MAXSIZE = 256


class _Channel(Protocol):
    async def send(self, frame: dict) -> None: ...


class _Observer:
    """单个观测连接：有界队列 + 独立 drain task 顺序送帧到其 channel。"""

    def __init__(self, channel: _Channel) -> None:
        self._channel = channel
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            frame = await self._queue.get()
            try:
                await self._channel.send(frame)
            except Exception:
                # 单次 send 失败只丢这一帧、继续下一帧：连接真死了会经会话 aclose 取消本
                # task + 注销；瞬时 send 错不该永久冻住直播（return 会让整条流哑掉）。
                # 非忙等——下一轮仍 await queue.get 阻塞到有新帧。
                continue

    def offer(self, frame: dict) -> None:
        """入队一帧；满则丢最旧腾位（best-effort，绝不阻塞发布方）。"""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def close(self) -> None:
        if not self._task.done():
            self._task.cancel()


class ThreadObserverHub:
    """thread_id → 观测者集合。登记/注销/发布。"""

    def __init__(self) -> None:
        self._observers: dict[str, dict[Any, _Observer]] = {}

    def add(self, thread_id: str, channel: _Channel) -> None:
        """登记 channel 为 thread_id 的观测者（重复登记同一 channel 幂等）。"""
        if not thread_id:
            return
        obs = self._observers.setdefault(thread_id, {})
        if channel not in obs:
            obs[channel] = _Observer(channel)

    def remove(self, thread_id: str, channel: _Channel) -> None:
        """注销某观测者并停其 drain task。"""
        obs = self._observers.get(thread_id)
        if not obs:
            return
        observer = obs.pop(channel, None)
        if observer is not None:
            observer.close()
        if not obs:
            self._observers.pop(thread_id, None)

    def remove_channel(self, channel: _Channel) -> None:
        """连接关闭：从所有 thread 注销该 channel（会话不知自己观测的是哪条时用）。"""
        for thread_id in list(self._observers):
            self.remove(thread_id, channel)

    def publish(self, thread_id: str, frame: dict) -> None:
        """把一帧 wire 事件扇给该 thread 的全部观测者（非阻塞、满即丢）。"""
        obs = self._observers.get(thread_id)
        if not obs:
            return
        for observer in obs.values():
            observer.offer(frame)

    def has_observers(self, thread_id: str) -> bool:
        return bool(self._observers.get(thread_id))

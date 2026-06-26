"""流式卡片更新的合并任务队列：任意时刻至多 1 个在跑 + 1 个待跑。

当一个任务还在跑时新入队的任务成为新的 pending；若已有 pending（尚未开跑），新任务
**替换**它——只有最新快照存活。每个入队任务都是"当前累积内容的一张快照"，用户可见状态
只关心最新一张，丢掉中间快照只是少一次 HTTP 往返（下一张快照 seq 更大，最终仍然胜出）。

价值：``Throttle`` 在字符阈值下可能频繁 fire，但本队列保证至多 1 个请求在途，实际
HTTP QPS 受单次往返时间限制，不会打爆飞书 CardKit 限流。

失败处理：任务异常仅记日志、不重试——下一次 enqueue 自然取代。调用方应在终态路径
enqueue 一张最终快照并 ``drain()``，确保即使中间更新失败用户也能看到完整内容。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from lumi.utils.logger import logger


class UpdateQueue:
    """合并队列：任意时刻至多 1 running + 1 pending。"""

    def __init__(self) -> None:
        self._running: asyncio.Task[Any] | None = None
        self._pending: Callable[[], Awaitable[Any]] | None = None

    def enqueue(self, task: Callable[[], Awaitable[Any]]) -> None:
        """调度 ``task``。若已有 pending（未开跑）则静默替换——只保留最新。

        无返回值：调用方不能依赖任务一定执行（可能在开跑前被取代）；需要等队列静默
        请 ``await drain()``。
        """
        self._pending = task
        if self._running is None or self._running.done():
            self._start_next()

    async def drain(self) -> None:
        """阻塞到队列完全空闲。

        循环是因为每个跑完的任务会在 finally 里链起新的 pending，await 后须复查尾态。
        ``CancelledError`` 向上传播；其它异常已在 _runner 内记录，吞掉以继续 drain。
        """
        while self._running is not None and not self._running.done():
            try:
                await self._running
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    def _start_next(self) -> None:
        """认领 pending 并启动它。由 enqueue 及任务完成时的 finally 调用。"""
        if self._pending is None:
            self._running = None
            return
        task = self._pending
        self._pending = None
        loop = asyncio.get_running_loop()

        async def _runner() -> None:
            try:
                await task()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"UpdateQueue: task failed: {e}")
            finally:
                # 链起跑动期间新入队的 pending；单线程 asyncio 无竞争。
                self._start_next()

        self._running = loop.create_task(_runner())

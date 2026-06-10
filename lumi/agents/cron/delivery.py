"""ResultDelivery：结果投递抽象基类和 DeliveryManager。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from lumi.agents.cron.run_log import RunRecord
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from textual.app import App


class ResultDelivery(ABC):
    """结果投递抽象基类，所有投递通道必须继承此类。

    子类需实现 ``deliver`` 方法将任务执行结果投递到目标通道。
    ``close`` 提供默认空实现，子类按需覆盖以释放资源。
    """

    @abstractmethod
    async def deliver(self, record: RunRecord, text: str) -> None:
        """将一次任务执行结果投递到目标通道。

        Args:
            record: 本次执行的完整记录（任务名/状态/耗时等元数据）。
            text: 面向用户的结果文本（成功为输出全文，失败为状态+错误）。
        """
        ...

    async def close(self) -> None:
        """释放通道资源，默认空实现，子类按需覆盖。"""


class DeliveryManager:
    """管理多个投递通道，负责广播和生命周期。

    通过 ``register`` / ``unregister`` 管理通道列表，
    ``broadcast`` 向所有已注册通道投递结果（单个通道失败不影响其他通道），
    ``close_all`` 关闭所有通道并清空列表。
    """

    def __init__(self) -> None:
        self._channels: list[ResultDelivery] = []

    def register(self, channel: ResultDelivery) -> None:
        """注册一个投递通道。

        Args:
            channel: 要注册的投递通道实例。
        """
        self._channels.append(channel)

    def unregister(self, channel: ResultDelivery) -> None:
        """移除一个投递通道。

        Args:
            channel: 要移除的投递通道实例。

        Raises:
            ValueError: 通道未注册。
        """
        self._channels.remove(channel)

    async def broadcast(self, record: RunRecord, text: str) -> None:
        """向所有已注册通道投递结果，单个通道失败不影响其他通道。

        Args:
            record: 本次执行的完整记录。
            text: 面向用户的结果文本。
        """
        for ch in self._channels:
            try:
                await ch.deliver(record, text)
            except Exception:
                logger.warning(
                    "投递到 %s 失败 (job=%s)",
                    type(ch).__name__,
                    record.job_name,
                    exc_info=True,
                )

    async def close_all(self) -> None:
        """关闭所有通道并释放资源，关闭后清空通道列表。"""
        for ch in self._channels:
            try:
                await ch.close()
            except Exception:
                logger.warning("关闭投递通道失败: %s", type(ch).__name__, exc_info=True)
        self._channels.clear()


class TUIDelivery(ResultDelivery):
    """将定时任务执行结果持久化到 TUI 通知面板。

    Args:
        app: LumiApp 实例，用于调用 ``add_notification()`` 向通知面板推送通知。
    """

    def __init__(self, app: "App") -> None:
        self._app = app

    async def deliver(self, record: RunRecord, text: str) -> None:
        """将任务执行结果持久化到通知面板。"""
        if not hasattr(self._app, "add_notification"):
            logger.warning(
                "[TUIDelivery] App 缺少 add_notification 方法，'%s' 的通知将不会展示",
                record.job_name,
            )
            return
        self._app.call_later(
            lambda: self._app.add_notification(
                record.job_name,
                text,
                started_at=record.started_at,
                duration_ms=record.duration_ms,
            )
        )


class APIDelivery(ResultDelivery):
    """通过 SSE 推送任务执行结果，无活跃连接时缓存结果。

    维护一个订阅者列表，每个订阅者对应一个 ``asyncio.Queue``。
    ``deliver`` 时向所有订阅者的 queue 推送消息；若无订阅者，
    则将结果放入缓存列表，待新订阅者连接后按顺序推送。

    Args:
        max_buffer: 无订阅者时缓存结果的最大数量，默认 50。
    """

    _SENTINEL = object()  # 用于通知订阅者关闭的哨兵值

    def __init__(self, max_buffer: int = 50) -> None:
        self._max_buffer = max_buffer
        self._buffer: list[dict[str, str | int | None]] = []
        self._subscribers: list[
            asyncio.Queue[dict[str, str | int | None] | object]
        ] = []

    async def deliver(self, record: RunRecord, text: str) -> None:
        """将任务执行结果投递给所有订阅者，无订阅者时缓存。"""
        message: dict[str, str | int | None] = {
            "job_name": record.job_name,
            "output": text,
            "started_at": record.started_at.isoformat(),
            "duration_ms": record.duration_ms,
        }

        if not self._subscribers:
            # 无活跃订阅者，放入缓存
            if len(self._buffer) >= self._max_buffer:
                # 缓存已满，丢弃最旧的结果
                self._buffer.pop(0)
            self._buffer.append(message)
            return

        # 向所有订阅者推送
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("SSE 订阅者队列已满，丢弃消息: %s", record.job_name)

    async def subscribe(self) -> AsyncGenerator[dict[str, str | int | None], None]:
        """创建一个 SSE 订阅，返回异步生成器。

        新订阅者连接后先接收所有缓存结果，然后持续等待新结果。
        生成器在 ``close()`` 被调用或订阅者断开时终止。

        Yields:
            包含 ``job_name``、``output``、``started_at``、``duration_ms`` 的字典。
        """
        queue: asyncio.Queue[dict[str, str | int | None] | object] = asyncio.Queue()
        self._subscribers.append(queue)

        try:
            # 先推送缓存中的结果
            buffered = list(self._buffer)
            self._buffer.clear()
            for msg in buffered:
                yield msg

            # 持续等待新结果
            while True:
                item = await queue.get()
                if item is self._SENTINEL:
                    break
                yield item  # type: ignore[misc]
        finally:
            # 清理：从订阅者列表中移除
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def close(self) -> None:
        """关闭所有订阅者连接并清理资源。"""
        for queue in self._subscribers:
            try:
                queue.put_nowait(self._SENTINEL)
            except asyncio.QueueFull:
                logger.warning("SSE 订阅者队列已满，无法发送关闭信号")
        self._subscribers.clear()
        self._buffer.clear()

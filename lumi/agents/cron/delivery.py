"""ResultDelivery：结果投递抽象基类和 DeliveryManager。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lumi.agents.cron.run_log import RunRecord
from lumi.utils.logger import logger


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

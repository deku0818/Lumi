"""进程级 IM channel 管理器（单例）。

``lumi serve`` 的 lifespan 经 :func:`channels_runtime` 起它，按 ``lumi.json`` 的 "channels" 分区
拉起已启用的 channel；desktop UI 经 WS RPC（``save_channel``）改配置后调 :meth:`reload`
停旧起新——channel 是进程级长连接，一个 serve 一条飞书连接，所有连上来的 UI 共享同一状态。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from lumi.gateway.channels.feishu.bridge_pool import BridgePool
from lumi.gateway.channels.store import load_feishu
from lumi.utils.logger import logger


class ChannelManager:
    """持有运行中的 IM channel、其传输任务与会话池；reload 时只重启传输。"""

    def __init__(self) -> None:
        self._channels: dict[str, object] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # BridgePool 跨「传输重连」存活：改凭证 / 拨开关只重启 WS 连接，不该清空进行中的
        # 会话（会话池由本 manager 拥有，只在禁用 / workspace 变更 / 进程退出时回收）。
        self._pools: dict[str, BridgePool] = {}
        # 串行化 reload，挡住并发 save_channel 在主 loop 交错建出重复 channel。
        self._reload_lock = asyncio.Lock()

    async def reload(self, cfg=None) -> None:
        """按配置对齐运行态：飞书启用则重启传输、禁用则停并回收会话。

        cfg 给定（save_channel 刚校验持久化的结果）则直接用，省一次重复读盘；否则读 store。
        """
        async with self._reload_lock:
            await self._apply_feishu(cfg if cfg is not None else load_feishu())

    async def _apply_feishu(self, cfg) -> None:
        await self._stop_transport("feishu")  # 只停旧 WS 传输，不动会话池
        if not cfg.enabled:
            await self._drop_pool("feishu")  # 禁用 → 连进行中的会话一并回收
            return
        pool = self._pools.get("feishu")
        if pool is None or pool.workspace != cfg.workspace:
            await self._drop_pool("feishu")  # 项目目录变了 → 换一套会话池
            pool = BridgePool(cfg.workspace)
            self._pools["feishu"] = pool
        from lumi.gateway.channels.feishu import FeishuChannel

        ch = FeishuChannel(cfg, bridge_pool=pool)
        self._channels["feishu"] = ch
        self._tasks["feishu"] = asyncio.create_task(ch.start(), name="im-feishu")
        logger.info("[ChannelManager] 飞书 channel 已启动")

    async def _stop_transport(self, name: str) -> None:
        """停掉某 channel 的传输（WS 连接 + 长跑任务），保留其会话池。"""
        ch = self._channels.pop(name, None)
        task = self._tasks.pop(name, None)
        if ch is not None:
            try:
                await ch.stop()
            except Exception as e:
                logger.warning(f"[ChannelManager] 停止 {name} 传输异常: {e}")
        if task is not None:
            task.cancel()

    async def _drop_pool(self, name: str) -> None:
        """回收某 channel 的会话池（关闭其全部 bridge）。"""
        pool = self._pools.pop(name, None)
        if pool is not None:
            await pool.close_all()

    async def stop_all(self) -> None:
        for name in list(self._channels):
            await self._stop_transport(name)
        for name in list(self._pools):
            await self._drop_pool(name)

    def list_channels(self) -> list[dict]:
        """供 ``get_channels`` RPC：每个 channel 的 name / enabled / config / status。"""
        cfg = load_feishu()
        ch = self._channels.get("feishu")
        if ch is not None:
            status = ch.status()
        elif cfg.enabled:
            status = {"state": "stopped", "detail": "未运行"}
        else:
            status = {"state": "off", "detail": "未启用"}
        return [
            {
                "name": "feishu",
                "enabled": cfg.enabled,
                "config": cfg.model_dump(),
                "status": status,
            }
        ]


# 进程级单例：serve lifespan 与 WS RPC 共享
manager = ChannelManager()


@asynccontextmanager
async def channels_runtime():
    """serve lifespan 复用：进入时按配置起 channel，退出时全停。"""
    await manager.reload()
    try:
        yield
    finally:
        await manager.stop_all()

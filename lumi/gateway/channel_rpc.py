"""Channel RPC：desktop WS 的 IM channel 管理方法实现。

进程级 ChannelManager 单例（``channels/manager.py``）由 serve lifespan 起；这些方法读写
``lumi.json`` 的 "channels" 分区并触发实时停旧起新。照抄 ``cron_rpc`` 的进程级分发范式。
"""

from __future__ import annotations

import asyncio

from lumi.gateway.channels.feishu import minutes, setup
from lumi.gateway.channels.manager import manager
from lumi.gateway.channels.store import save_feishu

CHANNEL_METHODS = frozenset(
    {
        "get_channels",
        "save_channel",
        "diagnose_minutes",
        "diagnose_feishu_setup",
    }
)


async def dispatch_channel(method: str, params: dict) -> dict:
    """执行一个 channel RPC 方法（method 已确认属于 CHANNEL_METHODS）。"""
    if method == "get_channels":
        return {"channels": manager.list_channels()}

    name = params.get("name") or "feishu"
    if name != "feishu":
        raise ValueError(f"暂不支持的 channel: {name}")
    config = params.get("config") or {}

    # 两个诊断都是同步的子进程 / 网络调用，丢线程池免得阻塞 WS 事件循环
    if method == "diagnose_minutes":
        checks = await asyncio.to_thread(minutes.diagnose, config.get("app_id") or "")
        return {"checks": checks}

    if method == "diagnose_feishu_setup":
        checks = await asyncio.to_thread(
            setup.diagnose,
            config.get("app_id") or "",
            config.get("app_secret") or "",
        )
        return {"checks": checks}

    # save_channel：校验 + 持久化（密钥 chmod 600），复用刚存的 cfg 停旧起新省一次读盘
    cfg = save_feishu(config)
    await manager.reload(cfg)
    return {"channels": manager.list_channels()}

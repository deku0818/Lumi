"""Channel RPC：desktop WS 的 IM channel 管理方法实现。

进程级 ChannelManager 单例（``channels/manager.py``）由 serve lifespan 起；这些方法读写
``~/.lumi/channels.json`` 并触发实时停旧起新。照抄 ``cron_rpc`` 的进程级分发范式。
"""

from __future__ import annotations

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.feishu.channel import test_credentials
from lumi.gateway.channels.manager import manager
from lumi.gateway.channels.store import save_feishu

CHANNEL_METHODS = frozenset({"get_channels", "save_channel", "test_channel"})


async def dispatch_channel(method: str, params: dict) -> dict:
    """执行一个 channel RPC 方法（method 已确认属于 CHANNEL_METHODS）。"""
    if method == "get_channels":
        return {"channels": manager.list_channels()}

    name = params.get("name") or "feishu"
    if name != "feishu":
        raise ValueError(f"暂不支持的 channel: {name}")
    config = params.get("config") or {}

    if method == "save_channel":
        cfg = save_feishu(config)  # 校验 + 持久化（密钥 chmod 600）
        await manager.reload(cfg)  # 复用刚存的 cfg 停旧起新，省一次读盘
        return {"channels": manager.list_channels()}

    # test_channel：用给定凭证临时验证连通性，不动正在运行的 channel
    return await test_credentials(FeishuChannelConfig.model_validate(config))

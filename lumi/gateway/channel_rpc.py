"""Channel RPC：desktop WS 的 IM channel 管理方法实现。

进程级 ChannelManager 单例（``channels/manager.py``）由 serve lifespan 起；这些方法读写
``lumi.json`` 的 "channels" 分区并触发实时停旧起新。照抄 ``cron_rpc`` 的进程级分发范式。
一台机器多个飞书机器人：save 按 ``config.id`` upsert，delete 按 ``bot_id`` 删。
"""

from __future__ import annotations

import asyncio

from lumi.gateway.channels.feishu import lark_profile, minutes, setup
from lumi.gateway.channels.manager import manager
from lumi.gateway.channels.store import (
    config_path,
    delete_feishu_bot,
    load_feishu_bots,
)
from lumi.utils.logger import logger

CHANNEL_METHODS = frozenset(
    {
        "get_channels",
        "save_channel",
        "delete_channel",
        "diagnose_minutes",
        "diagnose_feishu_setup",
    }
)


async def _reload_manager() -> None:
    """读盘拿最新机器人列表（线程池，不堵 WS 事件循环）后对齐运行态。"""
    await manager.reload(await asyncio.to_thread(load_feishu_bots))


async def dispatch_channel(method: str, params: dict) -> dict:
    """执行一个 channel RPC 方法（method 已确认属于 CHANNEL_METHODS）。"""
    if method == "get_channels":
        # config_path：凭证落盘的绝对路径，面板原样展示（`~/.lumi/lumi.json` 这种
        # 写法非技术用户看不懂，Windows 上尤甚）
        return {
            "channels": manager.list_channels(),
            "config_path": config_path(),
        }

    name = params.get("name") or "feishu"
    if name != "feishu":
        raise ValueError(f"暂不支持的 channel: {name}")
    config = params.get("config") or {}

    # 诊断/存删都含同步的磁盘 / 子进程 / 网络调用，一律丢线程池免得阻塞 WS 事件循环
    if method == "diagnose_minutes":
        checks = await asyncio.to_thread(
            minutes.diagnose,
            config.get("app_id") or "",
            config.get("cli_profile") or "",
        )
        return {"checks": checks}

    if method == "diagnose_feishu_setup":
        # 一站式清单：本地环境（cli / 专属身份 / 技能包，按绑定项目）在前，远程四项在后。
        # 本地是子进程、远程是网络，彼此无依赖，并行省下整个本地段的墙钟时间
        local, remote = await asyncio.gather(
            asyncio.to_thread(
                setup.local_env_checks,
                config.get("workspace") or "",
                config.get("id") or "",
                config.get("cli_profile") or "",
                config.get("app_id") or "",
            ),
            asyncio.to_thread(
                setup.diagnose,
                config.get("app_id") or "",
                config.get("app_secret") or "",
            ),
        )
        for check in remote:
            check["group"] = "机器人接入"
        return {"checks": local + remote}

    if method == "delete_channel":
        bot_id = params.get("bot_id") or ""
        removed = await asyncio.to_thread(delete_feishu_bot, bot_id)
        await _reload_manager()
        if removed is not None:
            # 回收专属 profile（其用户授权一并清掉），best-effort：lark-cli 不在也不碍删除
            await asyncio.to_thread(
                lark_profile.remove_profile,
                bot_id,
                removed.get("cli_profile") or "",
            )
        return {"channels": manager.list_channels()}

    # save_channel：校验 → 同步 lark-cli 专属身份 → 单次落盘（save_bot_synced，
    # CLI 同路径）。同步 best-effort——lark-cli 缺失/旧版不该挡保存，体检兜底
    cfg, notice = await asyncio.to_thread(lark_profile.save_bot_synced, config)
    if notice:
        logger.info(f"[channel_rpc] 机器人「{cfg.name}」profile 未同步: {notice}")
    await _reload_manager()
    return {"channels": manager.list_channels()}

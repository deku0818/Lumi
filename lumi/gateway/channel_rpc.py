"""Channel RPC：desktop WS 的 IM channel 管理方法实现。

进程级 ChannelManager 单例（``channels/manager.py``）由 serve lifespan 起；这些方法读写
``lumi.json`` 的 "channels" 分区并触发实时停旧起新。一台机器多个飞书机器人：save 按
``config.id`` upsert，delete 按 ``bot_id`` 删。飞书模块全部在 handler 内延迟 import：
导入 gateway.session 不应连带拉起 lark SDK。
"""

from __future__ import annotations

import asyncio

from lumi.gateway.channels.config import FeishuChannelConfig
from lumi.gateway.channels.store import (
    config_path,
    delete_feishu_bot,
    load_feishu_bots,
)
from lumi.utils.logger import logger


async def diagnose_bot(cfg: FeishuChannelConfig, *, minutes: bool) -> list[dict]:
    """一个机器人的接入体检清单：本地环境（cli / 专属身份 / 技能包）在前，远程四项
    （凭证 / 权限 / 事件 / 发布）在后，``minutes`` 为真再追加妙记四项。

    三组彼此无依赖（本地是子进程、远程是网络），并发跑省下整个本地段的墙钟时间——
    用默认执行器的 ``to_thread``，不自建线程池（调用方本就在事件循环里）。
    desktop RPC 与 `lumi feishu diagnose` CLI 共用的唯一实现。
    """
    from lumi.gateway.channels.feishu import minutes as minutes_mod
    from lumi.gateway.channels.feishu import setup

    jobs = [
        asyncio.to_thread(
            setup.local_env_checks, cfg.workspace, cfg.id, cfg.cli_profile, cfg.app_id
        ),
        asyncio.to_thread(setup.diagnose, cfg.app_id, cfg.app_secret),
    ]
    if minutes:
        jobs.append(
            asyncio.to_thread(minutes_mod.diagnose, cfg.app_id, cfg.cli_profile)
        )
    local, remote, *extra = await asyncio.gather(*jobs)
    checks = local + [{**c, "group": "机器人接入"} for c in remote]
    return checks + (extra[0] if extra else [])


async def _reload_manager() -> None:
    """读盘拿最新机器人列表（线程池，不堵 WS 事件循环）后对齐运行态。"""
    from lumi.gateway.channels.manager import manager

    await manager.reload(await asyncio.to_thread(load_feishu_bots))


def _list_channels() -> list[dict]:
    from lumi.gateway.channels.manager import manager

    return manager.list_channels()


async def _get_channels(params: dict) -> dict:
    # config_path：凭证落盘的绝对路径，面板原样展示（`~/.lumi/lumi.json` 这种
    # 写法非技术用户看不懂，Windows 上尤甚）
    return {"channels": _list_channels(), "config_path": config_path()}


# 诊断/存删都含同步的磁盘 / 子进程 / 网络调用，一律丢线程池免得阻塞 WS 事件循环


async def _diagnose_minutes(params: dict) -> dict:
    from lumi.gateway.channels.feishu import minutes

    config = params.get("config") or {}
    checks = await asyncio.to_thread(
        minutes.diagnose, config.get("app_id") or "", config.get("cli_profile") or ""
    )
    return {"checks": checks}


async def _diagnose_setup(params: dict) -> dict:
    cfg = FeishuChannelConfig.model_validate(params.get("config") or {})
    return {"checks": await diagnose_bot(cfg, minutes=False)}


async def _delete_channel(params: dict) -> dict:
    from lumi.gateway.channels.feishu import lark_profile

    bot_id = params.get("bot_id") or ""
    removed = await asyncio.to_thread(delete_feishu_bot, bot_id)
    await _reload_manager()
    if removed is not None:
        # 回收专属 profile（其用户授权一并清掉），best-effort：lark-cli 不在也不碍删除
        await asyncio.to_thread(
            lark_profile.remove_profile, bot_id, removed.get("cli_profile") or ""
        )
    return {"channels": _list_channels()}


async def _save_channel(params: dict) -> dict:
    # 校验 → 同步 lark-cli 专属身份 → 单次落盘（save_bot_synced，CLI 同路径）。
    # 同步 best-effort——lark-cli 缺失/旧版不该挡保存，体检兜底
    from lumi.gateway.channels.feishu import lark_profile

    # RPC 边界校验外部输入：目前只有飞书一种渠道，别的名字原样存成飞书机器人才是坑
    name = params.get("name") or "feishu"
    if name != "feishu":
        raise ValueError(f"暂不支持的 channel: {name}")

    cfg, notice = await asyncio.to_thread(
        lark_profile.save_bot_synced, params.get("config") or {}
    )
    if notice:
        logger.info(f"[channel_rpc] 机器人「{cfg.name}」profile 未同步: {notice}")
    await _reload_manager()
    return {"channels": _list_channels()}


HANDLERS = {
    "get_channels": _get_channels,
    "save_channel": _save_channel,
    "delete_channel": _delete_channel,
    "diagnose_minutes": _diagnose_minutes,
    "diagnose_feishu_setup": _diagnose_setup,
}

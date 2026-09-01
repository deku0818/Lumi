"""进程级 IM channel 管理器（单例）。

``lumi serve`` 的 lifespan 经 :func:`channels_runtime` 起它，按 ``lumi.json`` 的 "channels" 分区
拉起已启用的飞书机器人（每台机器可配多个，一机器人 = 一条 WS 长连接 + 一个会话池，
槽位按 ``bot.id`` keyed）；desktop UI 经 WS RPC（``save_channel``）改配置后调 :meth:`reload`
按条 diff 停旧起新——改哪个机器人只弹哪条连接，其余在途会话不受牵连。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from lumi.gateway.channels.feishu.bridge_pool import BridgePool
from lumi.gateway.channels.store import (
    config_path,
    load_feishu_bots,
    shell_env_for,
)
from lumi.utils.logger import logger


class ChannelManager:
    """持有运行中的机器人 channel、其传输任务与会话池；reload 按机器人 diff。"""

    def __init__(self) -> None:
        self._channels: dict[str, object] = {}  # bot.id → FeishuChannel
        self._tasks: dict[str, asyncio.Task] = {}
        # BridgePool 跨「传输重连」存活：改凭证 / 拨开关只重启 WS 连接，不该清空进行中的
        # 会话（会话池由本 manager 拥有，只在禁用 / workspace 变更 / 删除 / 进程退出时回收）。
        self._pools: dict[str, BridgePool] = {}
        # 串行化 reload，挡住并发 save_channel 在主 loop 交错建出重复 channel。
        self._reload_lock = asyncio.Lock()
        # 最后应用的机器人配置（bot.id → cfg）：既供 watch_store 判断磁盘变更是否真的
        # 动了本渠道（lumi.json 还有别的分区，光看 mtime 会白弹连接），也供 reload
        # 按条跳过没变的机器人。
        self._applied: dict[str, object] = {}

    async def reload(self, bots=None) -> None:
        """按配置对齐运行态：新增/变更的机器人重启传输、消失/禁用的停并回收会话。

        bots 给定（watch_store 刚读的列表）则直接用，省一次重复读盘；否则读 store。
        """
        async with self._reload_lock:
            await self._apply_feishu(bots if bots is not None else load_feishu_bots())

    async def _apply_feishu(self, bots: list) -> None:
        desired = {b.id: b for b in bots if b.enabled}
        # 消失/禁用的机器人：停传输 + 回收会话池
        for bot_id in [k for k in self._channels if k not in desired]:
            await self._stop_transport(bot_id)
        for bot_id in [k for k in self._pools if k not in desired]:
            await self._drop_pool(bot_id)
        for bot_id, cfg in desired.items():
            # 没变且在跑的机器人不折腾——按条 diff 正是多机器人的意义：改 A 不弹 B。
            # error 态（start() 失败留下的死对象）不算「在跑」：否则配置没变的
            # 「保存并重连」会被跳过，用户失去唯一的不重启恢复手段（connecting
            # 不在此列——WS 层每 5s 自动重试，自己会好）
            ch = self._channels.get(bot_id)
            if (
                self._applied.get(bot_id) == cfg
                and ch is not None
                and ch.status().get("state") != "error"
            ):
                continue
            await self._stop_transport(bot_id)  # 只停旧 WS 传输，不动会话池
            pool = self._pools.get(bot_id)
            # 只有项目变更才换一套会话池：bridge 的权限引擎 / hooks 在建桥时 pin 到项目根，
            # 不会自更新。模型与档位不在此列——它们按会话存、每轮开跑前对齐。
            if pool is None or pool.workspace != cfg.workspace:
                await self._drop_pool(bot_id)
                pool = BridgePool(cfg.workspace)
                self._pools[bot_id] = pool
            from lumi.gateway.channels.feishu import FeishuChannel

            ch = FeishuChannel(cfg, bridge_pool=pool)
            self._channels[bot_id] = ch
            self._tasks[bot_id] = asyncio.create_task(
                ch.start(), name=f"im-feishu-{bot_id}"
            )
            logger.info(f"[ChannelManager] 飞书机器人「{cfg.name}」已启动")
        self._applied = {b.id: b for b in bots}

    async def _stop_transport(self, bot_id: str) -> None:
        """停掉某机器人的传输（WS 连接 + 长跑任务），保留其会话池。"""
        ch = self._channels.pop(bot_id, None)
        task = self._tasks.pop(bot_id, None)
        if ch is not None:
            try:
                await ch.stop()
            except Exception as e:
                logger.warning(f"[ChannelManager] 停止机器人 {bot_id} 传输异常: {e}")
        if task is not None:
            task.cancel()

    async def _drop_pool(self, bot_id: str) -> None:
        """回收某机器人的会话池（关闭其全部 bridge）。"""
        pool = self._pools.pop(bot_id, None)
        if pool is not None:
            await pool.close_all()

    async def watch_store(self, interval: float = 3.0) -> None:
        """轮询 lumi.json，配置真变了才 reload——CLI（`lumi feishu config`）等
        进程外写入没有 RPC 通道可通知，靠这里在几秒内热生效，不用重启 serve。
        """
        last_mtime = None
        while True:
            await asyncio.sleep(interval)
            # 单轮失败不退出：这是 fire-and-forget 任务，异常逃逸会无声杀掉监视器，
            # 之后所有 CLI 写入都静默不生效直到重启 serve
            try:
                mtime = Path(config_path()).stat().st_mtime_ns
                if mtime == last_mtime:
                    continue
                bots = load_feishu_bots()
                if {b.id: b for b in bots} != self._applied:
                    logger.info("[ChannelManager] 检测到 channels 配置变更，热重载")
                    await self.reload(bots)
                # 成功处理完才推进：中途失败时保持旧值，下轮对同一次变更重试
                last_mtime = mtime
            except FileNotFoundError:
                continue
            except Exception:
                logger.warning(
                    "[ChannelManager] 配置监视一轮失败，下轮重试", exc_info=True
                )

    async def stop_all(self) -> None:
        for bot_id in list(self._channels):
            await self._stop_transport(bot_id)
        for bot_id in list(self._pools):
            await self._drop_pool(bot_id)

    def thread_lock(self, thread_id: str) -> asyncio.Lock | None:
        """渠道会话的运行锁（该 thread 已建桥时）。

        desktop「清空记忆」删渠道 thread 的 checkpoint 前持它，避开渠道在途轮——
        否则跑到一半的轮会把删掉的历史写回，清空静默失效。
        """
        for pool in self._pools.values():
            lock = pool.try_lock(thread_id)
            if lock is not None:
                return lock
        return None

    def list_channels(self) -> list[dict]:
        """供 ``get_channels`` RPC：每个机器人一条 name / enabled / config / status。"""
        out = []
        for cfg in load_feishu_bots():
            ch = self._channels.get(cfg.id)
            if ch is not None:
                status = ch.status()
            elif cfg.enabled:
                status = {"state": "stopped", "detail": "未运行"}
            else:
                status = {"state": "off", "detail": "未启用"}
            out.append(
                {
                    "name": "feishu",
                    "enabled": cfg.enabled,
                    "config": cfg.model_dump(),
                    "status": status,
                }
            )
        return out


# 进程级单例：serve lifespan 与 WS RPC 共享
manager = ChannelManager()


@asynccontextmanager
async def channels_runtime():
    """serve lifespan 复用：进入时按配置起 channel 并起配置监视，退出时全停。

    同时注册 shell env provider：项目绑了机器人，该项目所有会话的 Bash 里 lark-cli
    自动带上机器人专属 profile（LARKSUITE_CLI_PROFILE），项目间身份不串。
    """
    from lumi.agents.runtime.shell_session import set_shell_env_provider

    set_shell_env_provider(shell_env_for)
    await manager.reload()
    watcher = asyncio.create_task(manager.watch_store(), name="im-config-watch")
    try:
        yield
    finally:
        watcher.cancel()
        await manager.stop_all()

"""IM 渠道每日记忆整理：到点对有新消息的长会话先串行 dream、再并发 summary。

长会话（一群/一人一个常驻 thread）不走 Stop 钩子的增量 dream（见 dream.auto_dream_stop_hook
对渠道 thread 的抑制），改由本循环统一编排：

- **阶段一 · Dream（串行）**：共享同一 ``MEMORY.md``，一次只跑一个防写坏；每个 thread 持
  自己的 run-lock 跑长会话 dream（只综合当前会话）。lock 被用户轮占用则整批有限次重试，仍
  抢不到本轮整个跳过该会话（dream+summary 都不做），留到明天——不让单个活跃群卡死整批。
- **阶段二 · Summary（并发限流）**：各改各的 checkpoint，互不干扰；``Semaphore`` 限并发防
  接口 429。对刚 dream 成功的 thread 强制压缩历史（``AgentBridge.compact_thread``）。

判活：存在落库 ts 晚于该 thread 上次 dream 快照时刻（``dream_lock.read_thread_dreamed_at``）
的真实 human → 有新内容。基于时间戳而非消息计数，summary 压缩历史不影响判定。dream 成功
即由 ``consolidate_session_dream`` 写回该 thread 的快照时刻；dream 间互斥由
``_run_dream_fork`` 内的 per-project 锁兜底，本循环无需自管。

不依赖 FeishuChannel——只吃 ``(pool, config, channel_name)``，故未来新增 IM 渠道可直接复用。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path

from lumi.agents.memory import dream_lock
from lumi.agents.memory.dream import consolidate_session_dream
from lumi.gateway.broadcast import hub
from lumi.sessions.message_visibility import latest_human_ts
from lumi.utils.logger import logger

# run-lock 被用户轮占用时的重试：最多 N 次、每次隔 M 秒，耗尽则本轮跳过该会话。
_LOCK_ATTEMPTS = 3
_LOCK_RETRY_SECONDS = 180
# daily_dream_time 无法解析 / 未启用时的空转间隔。
_IDLE_SLEEP_SECONDS = 3600


def seconds_until_next(now: datetime, hhmm: str) -> float:
    """从 ``now`` 到下一个 ``HH:MM`` 的秒数（当日已过则顺延明日）。纯函数，供测试。"""
    hh, mm = (int(x) for x in hhmm.split(":", 1))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _acquire_idle_lock(pool, thread_id: str):
    """会话空闲时返回其 run-lock（未 acquire，调用方 ``async with`` 之）。

    有限次重试仍忙（或桥已回收）返回 ``None``。重试的 sleep **不**持任何并发配额。
    """
    for attempt in range(_LOCK_ATTEMPTS):
        lock = pool.try_lock(thread_id)
        if lock is None:
            return None
        if not lock.locked():
            return lock
        if attempt < _LOCK_ATTEMPTS - 1:
            await asyncio.sleep(_LOCK_RETRY_SECONDS)
    return None


async def _dream_one(bridge, thread_id: str) -> bool:
    """持锁跑单个长会话 dream，返回是否 dream **成功**（无新消息 / 综合失败则 False）。

    调用方已持锁。返回值把关 summary 阶段——先沉淀再压缩的次序只在 dream 成功时成立，
    失败就压会把未入记忆的历史永久压掉。dream 的异常被 bg-task 收尾吞掉（写 FAILED、
    不上抛），故成功与否看**快照时刻有没有推进**：``record_thread_dream`` 仅在综合
    成功后写入，是现成的成功信号。
    """
    project_dir = Path(bridge.workspace_dir)
    snapshot_ts = time.time()
    messages = await bridge.snapshot_messages()
    if latest_human_ts(messages) <= dream_lock.read_thread_dreamed_at(
        project_dir, thread_id
    ):
        return False  # 自上次 dream 以来无新消息
    # notify=False：定时维护静默完成，不往群里发任何汇报
    await consolidate_session_dream(
        project_dir, messages, thread_id, snapshot_ts, notify=False
    )
    return dream_lock.read_thread_dreamed_at(project_dir, thread_id) >= snapshot_ts


async def _dream_phase(pool) -> list[str]:
    """阶段一：串行 dream 有新消息的 thread，返回成功 dream 的 thread 列表。

    忙 thread 整批重试，``_LOCK_ATTEMPTS`` 轮后仍忙则本轮跳过（不进 summary 阶段）。
    """
    pending = list(pool.chat_ids)
    dreamed: list[str] = []
    for attempt in range(_LOCK_ATTEMPTS):
        still_busy: list[str] = []
        for thread_id in pending:
            lock = pool.try_lock(thread_id)
            if lock is None:
                continue  # 桥已回收
            if lock.locked():
                still_busy.append(thread_id)
                continue
            async with lock:
                bridge = await pool.get(thread_id)
                try:
                    if await _dream_one(bridge, thread_id):
                        dreamed.append(thread_id)
                except Exception:
                    logger.error(
                        "[daily-dream] dream 失败 thread=%s", thread_id, exc_info=True
                    )
        pending = still_busy
        if not pending:
            break
        if attempt < _LOCK_ATTEMPTS - 1:
            await asyncio.sleep(_LOCK_RETRY_SECONDS)
    if pending:
        logger.info(
            "[daily-dream] %d 个会话始终忙、本轮跳过：%s", len(pending), pending
        )
    return dreamed


async def _summary_phase(pool, config, channel_name: str, threads: list[str]) -> None:
    """阶段二：对刚 dream 成功的 thread 并发限流强制压缩历史。"""
    sem = asyncio.Semaphore(max(1, config.summary_max_concurrency))

    async def _summarize_one(thread_id: str) -> None:
        lock = await _acquire_idle_lock(pool, thread_id)  # 等锁时不占并发配额
        if lock is None:
            logger.info("[daily-dream] summary 跳过（始终忙）thread=%s", thread_id)
            return
        async with sem, lock:  # 并发配额只圈住真正的压缩调用
            try:
                bridge = await pool.get(thread_id)
                if await bridge.compact_thread():
                    hub.on_channel_activity(thread_id, channel_name)
            except Exception:
                logger.error(
                    "[daily-dream] summary 失败 thread=%s", thread_id, exc_info=True
                )

    await asyncio.gather(*(_summarize_one(t) for t in threads))


async def _run_cycle(pool, config, channel_name: str) -> None:
    """一次完整周期：全部 dream（串行）→ 屏障 → 全部 summary（并发限流）。"""
    dreamed = await _dream_phase(pool)
    if not dreamed:
        logger.info("[daily-dream] 本轮无会话需整理")
        return
    logger.info("[daily-dream] %d 个会话已 dream，开始并发 summary", len(dreamed))
    await _summary_phase(pool, config, channel_name, dreamed)


async def daily_dream_loop(pool, config, channel_name: str) -> None:
    """常驻循环：睡到 ``daily_dream_time`` → 跑一次周期 → 睡到次日。

    生命周期由渠道 ``start/stop`` 管理（config 变更经 manager.reload 重建渠道 → 本任务被取消
    重起，故 config 在单次任务生命内恒定）。
    """
    while True:
        if not config.enabled or not config.daily_dream_enabled:
            await asyncio.sleep(_IDLE_SLEEP_SECONDS)
            continue
        try:
            delay = seconds_until_next(datetime.now(), config.daily_dream_time)
        except (ValueError, AttributeError):
            logger.warning(
                "[daily-dream] 无法解析 daily_dream_time=%r，空转",
                config.daily_dream_time,
            )
            await asyncio.sleep(_IDLE_SLEEP_SECONDS)
            continue
        await asyncio.sleep(delay)
        try:
            await _run_cycle(pool, config, channel_name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[daily-dream] 周期执行异常", exc_info=True)

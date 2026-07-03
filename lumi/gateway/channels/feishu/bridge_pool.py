"""每会话 thread 一个常驻 AgentBridge 的池子（飞书等 IM channel 共用）。

IM 是单长连接承载 N 个用户/群，每个 chat 派生一个 thread_id，对应一个常驻 AgentBridge
（无断开信号，按用户决定不做 TTL 回收，进程存活期间一直驻留、复用 checkpoint）。每个
thread 配一把 ``asyncio.Lock`` 串行化本会话的轮次——同会话同一时刻只跑一条 stream，
避免并发 stream 撞坏 LangGraph 状态。
"""

from __future__ import annotations

import asyncio

from lumi.gateway.bridge import AgentBridge
from lumi.utils.logger import logger

# IM channel 的会话默认禁用的工具：飞书等不走 ask 询问卡片，关掉 ask 让模型自行判断
# 而非挂起等待（保留 auto/privileged 审批语义不变）。
IM_DISABLED_TOOLS = ["ask"]


class BridgePool:
    """thread_id → 常驻 AgentBridge + 运行锁。"""

    def __init__(
        self, workspace: str = "", disabled_tools: list[str] | None = None
    ) -> None:
        self._workspace = workspace
        # 默认禁用 ask（IM 不弹询问卡片）；显式传入则覆盖
        self._disabled_tools = (
            disabled_tools if disabled_tools is not None else IM_DISABLED_TOOLS
        )
        self._bridges: dict[str, AgentBridge] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # thread_id → chat_id：通知 poller 回投用。放池上（而非 FeishuInbound）
        # 是因为配置热重载保留池但重建 channel/inbound——映射跟着任务归属走，
        # 不能随传输层重建而丢。
        self.chat_ids: dict[str, str] = {}
        # thread_id → 当前用户轮的 run task（/stop 取消用），与 chat_ids 同理放池上。
        # 只登记用户轮：通知 poller 的轮跑在 poller task 自身里，cancel 会杀掉整个轮询。
        self.run_tasks: dict[str, asyncio.Task] = {}
        # 串行化"建桥"本身：首条消息并发到达同一新 thread 时只建一次
        self._init_lock = asyncio.Lock()

    @property
    def workspace(self) -> str:
        """本池所有 bridge 绑定的项目根（manager 据此判断 workspace 是否变更）。"""
        return self._workspace

    async def get(self, thread_id: str) -> AgentBridge:
        """取该 thread 的 AgentBridge，不存在则初始化一个并切到该 thread。"""
        async with self._init_lock:
            bridge = self._bridges.get(thread_id)
            if bridge is None:
                bridge = AgentBridge()
                await bridge.initialize(
                    self._workspace, disabled_tools=self._disabled_tools
                )
                bridge.switch_thread(thread_id)
                self._bridges[thread_id] = bridge
                self._locks[thread_id] = asyncio.Lock()
                logger.info(f"[BridgePool] 新建 AgentBridge thread={thread_id}")
            return bridge

    def peek(self, thread_id: str) -> AgentBridge | None:
        """已建桥则返回，否则 None（不隐式建桥——建桥重且常驻，只在真要跑轮时建）。"""
        return self._bridges.get(thread_id)

    def lock(self, thread_id: str) -> asyncio.Lock:
        """该 thread 的运行锁；建桥时一并创建，故此处必然存在。"""
        return self._locks[thread_id]

    def try_lock(self, thread_id: str) -> asyncio.Lock | None:
        """该 thread 的运行锁；未建桥（无此 thread）返回 None。"""
        return self._locks.get(thread_id)

    async def close_all(self) -> None:
        """回收全部 bridge（禁用 / workspace 变更 / 进程退出）。

        先 reject_pending 收尾挂起的 ask/审批让在途轮尽快释放运行锁，再等锁（5s 上限）
        确保不在某轮 run_turn 仍在用该 bridge 时 close——避免 use-after-close。
        """
        for thread_id, bridge in list(self._bridges.items()):
            try:
                bridge.reject_pending()
            except Exception:
                pass
            lock = self._locks.get(thread_id)
            if lock is not None and lock.locked():
                try:
                    # 池正被销毁，acquire 后不释放（无后续轮次）
                    await asyncio.wait_for(lock.acquire(), timeout=5.0)
                except TimeoutError:
                    logger.warning(
                        f"[BridgePool] thread={thread_id} 在途轮未在 5s 内结束，强制关闭"
                    )
            try:
                await bridge.close()
            except Exception as e:
                logger.warning(f"[BridgePool] 关闭 bridge thread={thread_id} 异常: {e}")
        self._bridges.clear()
        self._locks.clear()
        self.chat_ids.clear()
        self.run_tasks.clear()

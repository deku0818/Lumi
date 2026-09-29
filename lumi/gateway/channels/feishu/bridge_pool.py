"""每会话 thread 一个常驻 AgentBridge 的池子（飞书等 IM channel 共用）。

IM 是单长连接承载 N 个用户/群，每个 chat 派生一个 thread_id，对应一个常驻 AgentBridge
（无断开信号，按用户决定不做 TTL 回收，进程存活期间一直驻留、复用 checkpoint）。每个
thread 配一把 ``asyncio.Lock`` 串行化本会话的轮次——同会话同一时刻只跑一条 stream，
避免并发 stream 撞坏 LangGraph 状态。

池同时承载**须跨配置热重载存活的会话态**（投递地址 / 在跑的轮 / 待处理妙记事件）：
热重载保留池但重建 channel 及其 inbound，这些跟着会话走、不随传输层重建而丢。积压
消息仍归 inbound 私有，但每条积压都有自己的 task 在等池上的锁（见 inbound._admit），
重载后照样被接手。
"""

from __future__ import annotations

import asyncio

from lumi.gateway.bridge import AgentBridge
from lumi.utils.logger import logger

# IM channel 的会话禁用的工具：飞书等不走 ask 询问卡片，关掉 ask 让模型自行判断
# 而非挂起等待（保留 auto/privileged 审批语义不变）。
IM_DISABLED_TOOLS = ["ask"]


class BridgePool:
    """thread_id → 常驻 AgentBridge + 运行锁 + 跨重载的会话态。"""

    def __init__(self, workspace: str = "") -> None:
        self._workspace = workspace
        self._bridges: dict[str, AgentBridge] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # thread_id → 投递地址：通知 poller 回投用。值是 receive_id，群/私聊入站
        # 回填真实 chat_id（oc_），妙记推送到从未私聊过的人时回填 open_id（ou_）
        # ——两者都能投递，发送侧按前缀选 receive_id_type，故别拿它当 chat_id 用
        # （如传给 im.chat.get 查群名）。
        self.chat_ids: dict[str, str] = {}
        # thread_id → 当前用户轮的 run task（/stop 取消用）。只登记用户轮：通知 poller
        # 的轮跑在 poller task 自身里，cancel 会杀掉整个轮询。
        self.run_tasks: dict[str, asyncio.Task] = {}
        # 待处理的妙记事件；由通知轮询在会话空闲时认领
        self.minute_events: list = []
        # 串行化"建桥"本身：首条消息并发到达同一新 thread 时只建一次
        self._init_lock = asyncio.Lock()
        # 已进入回收：池不复用，等锁的积压者拿到锁也不再开新轮
        self.closed = False

    @property
    def workspace(self) -> str:
        """本池所有 bridge 绑定的项目根（manager 据此判断 workspace 是否变更）。"""
        return self._workspace

    async def get(self, thread_id: str) -> AgentBridge:
        """取该 thread 的 AgentBridge，不存在则初始化一个并切到该 thread。"""
        # 快路径不排 _init_lock：只让首次建桥串行，别的会话建桥时已有会话照常收消息
        if (bridge := self._bridges.get(thread_id)) is not None:
            return bridge
        async with self._init_lock:
            bridge = self._bridges.get(thread_id)
            if bridge is None:
                bridge = AgentBridge()
                await bridge.initialize(
                    self._workspace, disabled_tools=IM_DISABLED_TOOLS
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

    def busy(self, thread_id: str) -> bool:
        """本会话是否有轮在跑（不建桥、不建锁：从未建桥的会话必然空闲）。"""
        lock = self.try_lock(thread_id)
        return lock is not None and lock.locked()

    def try_lock(self, thread_id: str) -> asyncio.Lock | None:
        """该 thread 的运行锁；未建桥（无此 thread）返回 None。"""
        return self._locks.get(thread_id)

    async def close_all(self) -> None:
        """回收全部 bridge（禁用 / workspace 变更 / 进程退出）。

        先收尾挂起的 ask/审批、取消在途用户轮，再并发等各会话的运行锁（总共 5s 上限）
        ——不在某轮仍在用该 bridge 时 close（use-after-close）。持 _init_lock：get()
        正在建的新桥不会在清空后被丢弃、漏关。
        """
        async with self._init_lock:
            self.closed = True
            for bridge in self._bridges.values():
                bridge.reject_pending()
            for task in self.run_tasks.values():
                task.cancel()
            # 池正被销毁，acquire 后不释放（无后续轮次）
            acquires = [lock.acquire() for lock in self._locks.values()]
            try:
                await asyncio.wait_for(asyncio.gather(*acquires), timeout=5.0)
            except TimeoutError:
                logger.warning("[BridgePool] 在途轮未在 5s 内结束，强制关闭")
            for thread_id, bridge in self._bridges.items():
                try:
                    await bridge.close()
                except Exception as e:
                    logger.warning(
                        f"[BridgePool] 关闭 bridge thread={thread_id} 异常: {e}"
                    )
            self._bridges.clear()
            self._locks.clear()
            self.chat_ids.clear()
            self.run_tasks.clear()

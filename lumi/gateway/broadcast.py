"""进程级广播中枢（BroadcastHub）。

把 cron 运行状态/结果、后台任务变更扇出给所有活跃连接。进程级单例、跨 channel
共享——当前服务 desktop WS，未来 IM channel 复用同一 hub，无需各自重写广播逻辑。
（从 server/ws.py 的模块全局提取，消除「广播绑死 WS 模块」的耦合，见重构计划 M2。）
"""

from __future__ import annotations

import asyncio

from lumi.agents.runtime.bg_tasks import get_task_registry, serialize_task
from lumi.gateway.desktop_delivery import DesktopDelivery


def serialize_bg_tasks() -> list[dict]:
    """全部后台任务的快照（前端按当前 thread_id 过滤）。"""
    return [serialize_task(e) for e in get_task_registry().all_tasks()]


class BroadcastHub:
    """所有 channel 共享的进程级广播扇出。

    持有结果投递 sink（DesktopDelivery），并把 Scheduler / TaskRegistry 的同步回调
    转成对所有活跃连接的事件广播；后台任务变更带 ~100ms 去抖（高频扇出合并为一次
    全量快照，最终态必发）。
    """

    def __init__(self) -> None:
        self._delivery = DesktopDelivery()
        # 事件循环只弱引用 task，自持引用避免广播 task 在执行前被 GC
        self._tasks: set[asyncio.Task] = set()
        self._bg_dirty = False
        self._bg_flush_scheduled = False

    @property
    def delivery(self) -> DesktopDelivery:
        """供 DeliveryManager 注册的结果投递 sink。"""
        return self._delivery

    def register(self, channel) -> None:
        """连接建立时注册到广播通道。"""
        self._delivery.register(channel)

    def unregister(self, channel) -> None:
        """连接断开时注销。"""
        self._delivery.unregister(channel)

    def _spawn(self, coro) -> None:
        """fire-and-forget 一个广播协程，自持引用避免执行前被 GC。"""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_cron_job_status(self, names: list[str]) -> None:
        """Scheduler 同步回调：把运行中任务名列表广播为 cron.running 事件。"""
        self._spawn(self._delivery.send_event("cron.running", {"names": names}))

    def on_channel_activity(self, thread_id: str, channel: str) -> None:
        """IM channel 跑完一轮：广播给所有连接（desktop 刷会话列表 / 旁观视图重载）。"""
        self._spawn(
            self._delivery.send_event(
                "channel.activity", {"thread_id": thread_id, "channel": channel}
            )
        )

    def on_mcp_status(self, payload: dict) -> None:
        """MCP 池后台加载完成：广播各 server 结果（前端对失败项 toast / 面板刷徽标）。"""
        self._spawn(self._delivery.send_event("mcp.status", payload))

    def on_session_title(self, thread_id: str, title: str) -> None:
        """会话标题自动生成完成：广播给所有连接更新侧栏该会话的显示名。"""
        self._spawn(
            self._delivery.send_event(
                "session.title", {"thread_id": thread_id, "title": title}
            )
        )

    def on_bg_task_change(self) -> None:
        """TaskRegistry 同步回调：标脏并安排一次去抖广播（全量快照，前端按 thread 过滤）。"""
        self._bg_dirty = True
        self._schedule_bg_flush()

    def _schedule_bg_flush(self) -> None:
        if self._bg_flush_scheduled:
            return
        self._bg_flush_scheduled = True
        self._spawn(self._bg_flush())

    async def _bg_flush(self) -> None:
        try:
            await asyncio.sleep(0.1)  # 合并窗口
            self._bg_dirty = False
            await self._delivery.send_event(
                "bg_tasks.update", {"tasks": serialize_bg_tasks()}
            )
        finally:
            self._bg_flush_scheduled = False
        if self._bg_dirty:  # 窗口内又有新变更 → 补发一次，保证最终态送达
            self._schedule_bg_flush()


# 进程级单例：所有 channel 共享
hub = BroadcastHub()

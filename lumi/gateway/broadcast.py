"""进程级广播中枢（BroadcastHub）。

把 cron 运行状态/结果、后台任务变更扇出给所有活跃连接。进程级单例、跨 channel
共享——当前服务 desktop WS，未来 IM channel 复用同一 hub，无需各自重写广播逻辑。
（从 server/ws.py 的模块全局提取，消除「广播绑死 WS 模块」的耦合，见重构计划 M2。）
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from lumi.agents.runtime.bg_tasks import get_task_registry, serialize_task
from lumi.gateway.desktop_delivery import DesktopDelivery
from lumi.gateway.observers import ThreadObserverHub


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
        # cron 执行直播：thread_id → 观测者。cron runner 经 publish_thread_event 扇事件，
        # 桌面打开运行中的 cron 线程时经 add_observer 订阅（见 GatewaySession）。
        self._observers = ThreadObserverHub()

    @property
    def delivery(self) -> DesktopDelivery:
        """供 DeliveryManager 注册的结果投递 sink。"""
        return self._delivery

    def register(self, channel, mcp_key: Callable[[], str] | None = None) -> None:
        """连接建立时注册到广播通道；mcp_key 声明该连接绑定的 MCP 池（见 DesktopDelivery）。"""
        self._delivery.register(channel, mcp_key)

    def unregister(self, channel) -> None:
        """连接断开时注销。"""
        self._delivery.unregister(channel)

    def _spawn(self, coro) -> None:
        """fire-and-forget 一个广播协程，自持引用避免执行前被 GC。"""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_cron_job_status(self, runs: list[dict]) -> None:
        """Scheduler 同步回调：广播运行中任务快照为 cron.running。

        每条含 ``{job_id, thread_id, started_at}``——前端据此既标「运行中」job，也在
        执行记录顶部显示可点进观测的活条目（thread_id 非空时）。
        """
        self._spawn(self._delivery.send_event("cron.running", {"runs": runs}))

    # -- cron 执行直播：观测者登记 + 事件发布 --

    def add_observer(self, thread_id: str, channel) -> None:
        """桌面打开运行中的 cron 线程时登记为观测者。"""
        self._observers.add(thread_id, channel)

    def remove_observer_channel(self, channel) -> None:
        """连接关闭：从所有 thread 注销该 channel。"""
        self._observers.remove_channel(channel)

    def has_observers(self, thread_id: str) -> bool:
        """该 thread 是否有观测者——runner 据此短路 bridge_event_to_wire，零观测者不白建帧。"""
        return self._observers.has_observers(thread_id)

    def publish_thread_event(self, thread_id: str, frame: dict) -> None:
        """cron runner 逐事件发布到该 thread 的观测者（非阻塞、满即丢）。"""
        self._observers.publish(thread_id, frame)

    def on_cron_jobs_changed(self) -> None:
        """JobStore 同步回调：任务增删改后广播 cron.jobs，前端据此重拉任务列表。

        信号式（不带列表）：任务是跨机器 fan-out 的，前端收到即各机器重拉一次。
        """
        self._spawn(self._delivery.send_event("cron.jobs", {}))

    def on_channel_activity(self, thread_id: str, channel: str) -> None:
        """IM channel 跑完一轮：广播给所有连接（desktop 刷会话列表 / 旁观视图重载）。"""
        self._spawn(
            self._delivery.send_event(
                "channel.activity", {"thread_id": thread_id, "channel": channel}
            )
        )

    def on_mcp_status(self, payload: dict) -> None:
        """MCP 池后台加载完成：只发给绑定该池的连接（"" = 全局池 ↔ 无项目连接）。

        池 key 与连接 workspace 是后端 resolve 过的同源路径，服务端过滤后前端
        收到即与本连接相关——无需再比路径，也收不到别的机器/项目池的噪音。
        """

        def _mine(channel: object) -> bool:
            key_fn = self._delivery.mcp_key_of(channel)
            return key_fn is not None and key_fn() == payload["project"]

        self._spawn(self._delivery.send_event("mcp.status", payload, match=_mine))

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

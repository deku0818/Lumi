"""进程级广播中枢（BroadcastHub）。

把 cron 运行状态 / 结果、后台任务变更、渠道活动、MCP 池状态扇出给所有活跃连接，
并承载 cron 执行直播的按 thread 观测 pub/sub。进程级单例、跨 channel 共享。

**直播不变量**：观测者绝不背压 run。每观测者一条有界队列，满即丢最旧一条（实时视图
短暂落后，run 完成后 loadHistory 从 checkpoint 重对齐），发布侧永不 await 慢连接。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from lumi.agents.cron.delivery import ResultDelivery
from lumi.agents.cron.run_log import RunRecord
from lumi.agents.runtime.bg_tasks import get_task_registry, serialize_task
from lumi.gateway.channel import Channel
from lumi.gateway.protocol import ServerEvent, event_frame
from lumi.utils.logger import logger

# 每观测者队列上限：token 级 delta 高频，256 帧足够缓冲一小段网络抖动；
# 满了丢最旧、保持流向前推进（丢失的增量由完成后的 loadHistory 修正）。
_QUEUE_MAXSIZE = 256


def serialize_bg_tasks() -> list[dict]:
    """全部后台任务的快照（前端按当前 thread_id 过滤）。"""
    return [serialize_task(e) for e in get_task_registry().all_tasks()]


class _Observer:
    """单个观测连接：有界队列 + 独立 drain task 顺序送帧到其 channel。"""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            frame = await self._queue.get()
            try:
                await self._channel.send(frame)
            except Exception:
                # 单次 send 失败只丢这一帧、继续下一帧：连接真死了会经会话 aclose 取消本
                # task + 注销；瞬时 send 错不该永久冻住直播（return 会让整条流哑掉）。
                continue

    def offer(self, frame: dict) -> None:
        """入队一帧；满则丢最旧腾位（best-effort，绝不阻塞发布方）。"""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def close(self) -> None:
        self._task.cancel()  # 对已完成的 task 是 no-op


# 事件循环只弱引用 task：自持一份引用，避免 fire-and-forget 的协程执行前被 GC。
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    """fire-and-forget 一个协程并持有引用直到完成（广播 / 后台安装共用）。"""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class BroadcastHub(ResultDelivery):
    """所有 channel 共享的进程级广播扇出。

    也是 cron ``DeliveryManager`` 注册的结果投递 sink（``deliver`` → cron.result）。
    Scheduler / TaskRegistry 的同步回调经 ``on_*`` 转成对所有活跃连接的事件广播；
    后台任务变更带 ~100ms 去抖（高频扇出合并为一次全量快照，最终态必发）。
    """

    def __init__(self) -> None:
        # channel → 其绑定的 MCP 池 key 回调（None = 未声明，收不到 mcp.status 定向广播）
        self._channels: dict[Channel, Callable[[], str] | None] = {}
        self._bg_dirty = False
        self._bg_flush_scheduled = False
        # cron 执行直播：thread_id → {channel: 观测者}
        self._observers: dict[str, dict[Channel, _Observer]] = {}

    def register(
        self, channel: Channel, mcp_key: Callable[[], str] | None = None
    ) -> None:
        """连接建立时注册；mcp_key 声明该连接绑定的 MCP 池（live 回调，随 set_workspace
        跟随）——注册即声明，路由元数据与连接同进同出。"""
        self._channels[channel] = mcp_key

    def unregister(self, channel: Channel) -> None:
        """连接断开时注销。"""
        self._channels.pop(channel, None)

    async def send_event(
        self,
        event_type: str,
        payload: dict,
        match: Callable[[Channel], bool] | None = None,
    ) -> None:
        """向活跃连接广播一个 wire 事件（match 给定时只发匹配的连接），
        单条连接失败不影响其他连接。"""
        frame = event_frame(event_type, "", payload)
        for channel in list(self._channels):
            if match is not None and not match(channel):
                continue
            try:
                await channel.send(frame)
            except Exception:
                # 瞬时发送失败不剔除连接——连接生死由 register/unregister 管理，
                # 否则一次背压就会把活连接永久踢出后续所有广播。
                logger.warning("[BroadcastHub] 推送 %s 失败", event_type, exc_info=True)

    # -- cron --

    async def deliver(self, record: RunRecord, text: str) -> None:
        """ResultDelivery：任务执行结果广播为 cron.result。

        output 截断到 200 字符：前端只用它做通知摘要，完整结果经
        list_cron_runs 从 RunLog 读取，没必要向每条连接广播全文。
        """
        await self.send_event(
            ServerEvent.CRON_RESULT,
            {
                "job_id": record.job_id,
                "job_name": record.job_name,
                "status": record.status,
                "output": text[:200],
                "started_at": record.started_at.isoformat(),
                "duration_ms": record.duration_ms,
                # 前端据此按 run 追踪未读（看一条消一条）；空串=本次执行无可跳转会话
                "thread_id": record.thread_id,
            },
        )

    def on_cron_job_status(self, runs: list[dict]) -> None:
        """Scheduler 同步回调：广播运行中任务快照为 cron.running。

        每条含 ``{job_id, thread_id, started_at}``——前端据此既标「运行中」job，也在
        执行记录顶部显示可点进观测的活条目（thread_id 非空时）。
        """
        spawn(self.send_event(ServerEvent.CRON_RUNNING, {"runs": runs}))

    def on_cron_jobs_changed(self) -> None:
        """JobStore 同步回调：任务增删改后广播 cron.jobs，前端据此重拉任务列表。

        信号式（不带列表）：任务是跨机器 fan-out 的，前端收到即各机器重拉一次。
        """
        spawn(self.send_event(ServerEvent.CRON_JOBS, {}))

    # -- cron 执行直播：观测者登记 + 事件发布 --

    def add_observer(self, thread_id: str, channel: Channel) -> None:
        """桌面打开运行中的 cron 线程时登记为观测者（重复登记同一 channel 幂等）。"""
        if not thread_id:
            return
        obs = self._observers.setdefault(thread_id, {})
        if channel not in obs:
            obs[channel] = _Observer(channel)

    def remove_observer_channel(self, channel: Channel) -> None:
        """连接关闭：从所有 thread 注销该 channel 并停其 drain task。"""
        for thread_id in list(self._observers):
            obs = self._observers[thread_id]
            observer = obs.pop(channel, None)
            if observer is not None:
                observer.close()
            if not obs:
                del self._observers[thread_id]

    def has_observers(self, thread_id: str) -> bool:
        """该 thread 是否有观测者——runner 据此短路 bridge_event_to_wire，零观测者不白建帧。"""
        return bool(self._observers.get(thread_id))

    def publish_thread_event(self, thread_id: str, frame: dict) -> None:
        """cron runner 逐事件发布到该 thread 的观测者（非阻塞、满即丢）。"""
        for observer in self._observers.get(thread_id, {}).values():
            observer.offer(frame)

    # -- 渠道 / MCP / 标题 / 后台任务 --

    def on_channel_activity(self, thread_id: str, channel: str) -> None:
        """IM channel 跑完一轮：广播给所有连接（desktop 刷会话列表 / 旁观视图重载）。"""
        spawn(
            self.send_event(
                ServerEvent.CHANNEL_ACTIVITY,
                {"thread_id": thread_id, "channel": channel},
            )
        )

    def on_mcp_status(self, payload: dict) -> None:
        """MCP 池后台加载完成：只发给绑定该池的连接（"" = 全局池 ↔ 无项目连接）。

        池 key 与连接 workspace 是后端 resolve 过的同源路径，服务端过滤后前端
        收到即与本连接相关——无需再比路径，也收不到别的机器/项目池的噪音。
        """

        def _mine(channel: Channel) -> bool:
            key_fn = self._channels.get(channel)
            return key_fn is not None and key_fn() == payload["project"]

        spawn(self.send_event(ServerEvent.MCP_STATUS, payload, match=_mine))

    def on_session_title(self, thread_id: str, title: str) -> None:
        """会话标题自动生成完成：广播给所有连接更新侧栏该会话的显示名。"""
        spawn(
            self.send_event(
                ServerEvent.SESSION_TITLE, {"thread_id": thread_id, "title": title}
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
        spawn(self._bg_flush())

    async def _bg_flush(self) -> None:
        try:
            await asyncio.sleep(0.1)  # 合并窗口
            self._bg_dirty = False
            await self.send_event(
                ServerEvent.BG_TASKS_UPDATE, {"tasks": serialize_bg_tasks()}
            )
        finally:
            self._bg_flush_scheduled = False
        if self._bg_dirty:  # 窗口内又有新变更 → 补发一次，保证最终态送达
            self._schedule_bg_flush()


# 进程级单例：所有 channel 共享
hub = BroadcastHub()

"""DesktopDelivery：把 cron 任务结果广播为 desktop WS wire 事件。

放在 gateway 层而非 agents/cron/delivery.py——wire 信封格式
（{method:"event", params:{type, session_id, payload}}）由 gateway 协议层（protocol.py）持有，
agents 层只定义 ResultDelivery 抽象。
"""

from __future__ import annotations

from collections.abc import Callable

from lumi.agents.cron.delivery import ResultDelivery
from lumi.agents.cron.run_log import RunRecord
from lumi.gateway.protocol import event_frame
from lumi.utils.logger import logger


class DesktopDelivery(ResultDelivery):
    """把任务执行结果作为 wire 事件广播给所有活跃的 desktop 连接（Channel）。

    连接建立时 ``register``、断开时 ``unregister``。无活跃连接时不缓存——
    结果已落 RunLog，前端重连后通过 ``list_cron_runs`` 查询。

    除任务结果（cron.result）外，也承载运行状态广播（cron.running），
    供 serve 端把 Scheduler 的 on_job_status 回调推给前端。
    """

    def __init__(self) -> None:
        # channel（任何带 async send(dict) 的传输对象，如 WsChannel）→ 其绑定的
        # MCP 池 key 回调（None = 未声明绑定，收不到 mcp.status 定向广播）
        self._channels: dict[object, Callable[[], str] | None] = {}

    def register(self, channel, mcp_key: Callable[[], str] | None = None) -> None:
        """注册一条活跃连接（Channel）。

        mcp_key 声明该连接绑定的 MCP 池（live 回调，随 set_workspace 跟随）——
        注册即声明，路由元数据与连接同进同出，不靠事后贴属性。
        """
        self._channels[channel] = mcp_key

    def unregister(self, channel) -> None:
        """注销一条连接（连接断开时调用）。"""
        self._channels.pop(channel, None)

    def mcp_key_of(self, channel) -> Callable[[], str] | None:
        """channel 注册时声明的池 key 回调（未声明返回 None）。"""
        return self._channels.get(channel)

    async def send_event(
        self,
        event_type: str,
        payload: dict,
        match: Callable[[object], bool] | None = None,
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
                # 瞬时发送失败不剔除连接——连接生死由 register/unregister
                # 管理（WS 端点断开时调 unregister），否则一次背压就会把活连接
                # 永久踢出后续所有广播。
                logger.warning(
                    "[DesktopDelivery] 推送 %s 失败", event_type, exc_info=True
                )

    async def deliver(self, record: RunRecord, text: str) -> None:
        """将任务执行结果广播为 cron.result 事件。

        output 截断到 200 字符：前端只用它做通知摘要，完整结果经
        list_cron_runs 从 RunLog 读取，没必要向每条连接广播全文。
        """
        await self.send_event(
            "cron.result",
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

    async def close(self) -> None:
        """清空连接集合（连接生命周期由 WS 端点管理，无需主动关闭）。"""
        self._channels.clear()

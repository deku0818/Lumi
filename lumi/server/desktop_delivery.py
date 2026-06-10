"""DesktopDelivery：把 cron 任务结果广播为 desktop WS wire 事件。

放在 server 层而非 agents/cron/delivery.py——wire 信封格式
（{method:"event", params:{type, session_id, payload}}）由 server 协议层持有，
agents 层只定义 ResultDelivery 抽象。
"""

from __future__ import annotations

from datetime import datetime

from lumi.agents.cron.delivery import ResultDelivery
from lumi.utils.logger import logger


class DesktopDelivery(ResultDelivery):
    """把任务执行结果作为 wire 事件广播给所有活跃的 desktop WS 连接。

    连接建立时 ``register_ws``、断开时 ``unregister_ws``。无活跃连接时不缓存——
    结果已落 RunLog，前端重连后通过 ``list_cron_runs`` 查询。

    除任务结果（cron.result）外，也承载运行状态广播（cron.running），
    供 serve 端把 Scheduler 的 on_job_status 回调推给前端。
    """

    def __init__(self) -> None:
        # 任何带 async send_json(dict) 的连接对象（fastapi WebSocket）
        self._sockets: set = set()

    def register_ws(self, ws) -> None:
        """注册一条活跃 WS 连接。"""
        self._sockets.add(ws)

    def unregister_ws(self, ws) -> None:
        """注销一条 WS 连接（连接断开时调用）。"""
        self._sockets.discard(ws)

    async def send_event(self, event_type: str, payload: dict) -> None:
        """向所有活跃连接广播一个 wire 事件，单条连接失败不影响其他连接。"""
        frame = {
            "method": "event",
            "params": {"type": event_type, "session_id": "", "payload": payload},
        }
        for ws in list(self._sockets):
            try:
                await ws.send_json(frame)
            except Exception:
                logger.warning("[DesktopDelivery] 推送 %s 失败", event_type)
                self._sockets.discard(ws)

    async def deliver(
        self,
        job_name: str,
        output: str,
        *,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
        job_id: str = "",
        status: str = "success",
    ) -> None:
        """将任务执行结果广播为 cron.result 事件。

        Args:
            job_name: 任务名称。
            output: 任务执行结果文本。
            started_at: 任务开始执行的时间。
            duration_ms: 任务执行耗时（毫秒）。
            job_id: 任务 ID。
            status: 执行状态（success/failed/timeout）。
        """
        await self.send_event(
            "cron.result",
            {
                "job_id": job_id,
                "job_name": job_name,
                "status": status,
                "output": output,
                "started_at": started_at.isoformat() if started_at else None,
                "duration_ms": duration_ms,
            },
        )

    async def close(self) -> None:
        """清空连接集合（连接生命周期由 WS 端点管理，无需主动关闭）。"""
        self._sockets.clear()

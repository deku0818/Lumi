"""在途审批 Broker —— 以 asyncio.Future 替代 interrupt() 的中断-恢复。

节点 / 工具侧 ``await broker.request(payload, reject_value)`` 原地挂起，请求经
``adispatch_custom_event`` 发出（在 ``astream_events`` 以 ``on_custom_event`` 浮现，
自带 run_id / parent_ids）；会话层收到应答后 ``broker.resolve(approval_id, decision)``
解 Future，节点 await 立刻返回、就地续跑。不依赖 checkpoint 重放，按 ``approval_id``
寻址，天然支持并发多审批（主 agent + 多个子 / 外部 agent 同时挂起互不串扰）。

stop / 切会话经 ``reject_all()`` 把挂起请求按各自的 ``reject_value`` 收尾——本轮以拒绝
干净完成、保留历史，而非取消丢弃。
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import adispatch_custom_event

# astream_events 中浮现该名的 on_custom_event 即一次审批请求；bridge 据此 yield 审批卡片。
LUMI_APPROVAL_EVENT = "lumi_approval"


class ApprovalBroker:
    """按 approval_id 寻址的 Future 注册表，连接节点层（挂起）与会话层（应答）。"""

    def __init__(self) -> None:
        # approval_id → (future, reject_value)：reject_value 由请求方给出，是该请求被
        # stop / 切会话收尾时喂入的"拒绝"决策（tool_approval 为拒绝 dict，ask 为取消哨兵）。
        self._pending: dict[str, tuple[asyncio.Future, Any]] = {}

    async def request(self, payload: dict[str, Any], reject_value: Any) -> Any:
        """发出一次审批请求并原地挂起，直到被 resolve / reject_all。

        dispatch 在 await 之前同步完成，故审批卡片先抵达客户端，节点随后才挂起。
        reject_value：本请求被 stop / 切会话收尾时返回的拒绝决策，使本轮以拒绝干净完成。
        """
        approval_id = uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = (fut, reject_value)
        try:
            await adispatch_custom_event(
                LUMI_APPROVAL_EVENT, {"approval_id": approval_id, **payload}
            )
            return await fut
        finally:
            self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, decision: Any) -> bool:
        """以用户决策唤醒挂起的请求；返回是否命中一个未决请求。"""
        entry = self._pending.get(approval_id)
        if entry is None or entry[0].done():
            return False
        entry[0].set_result(decision)
        return True

    def reject_all(self) -> int:
        """以各请求自带的 reject_value 收尾全部挂起请求，返回处理数。

        stop / 切会话用它让本轮以拒绝干净完成、保留历史，而非取消丢弃。无挂起时返回 0。
        """
        count = 0
        for fut, reject_value in list(self._pending.values()):
            if not fut.done():
                fut.set_result(reject_value)
                count += 1
        return count

"""断连续接（Case 1）：进程内「已断开但仍挂着活跃轮」的会话登记表。

WS 断开时若会话还有活跃 / 挂起轮（如挂在审批上），不 aclose，而是 detach 进此表；同
thread 的 WS 重连时从此表取回、接上新 channel 继续——parked turn / broker / 挂起 Future
原样还在，无需 checkpoint 重放。仅 detached 会话在表内；干净关闭 / TTL 到期即移除。

进程级单例：一个 sidecar 进程内全部 WS 连接共享（每 thread 至多一个 detached 会话）。
扛后端进程重启不在范围内（in-memory，见 docs/architecture/approval-inflight.md「后续」）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lumi.gateway.session import GatewaySession


class SessionRegistry:
    """thread_id → 已 detached 的 GatewaySession。"""

    def __init__(self) -> None:
        self._detached: dict[str, GatewaySession] = {}

    def add(self, thread_id: str, session: GatewaySession) -> GatewaySession | None:
        """登记一个 detached 会话；返回被顶替的同 thread 旧会话（罕见，调用方 aclose 它）。"""
        displaced = self._detached.get(thread_id)
        self._detached[thread_id] = session
        return displaced if displaced is not session else None

    def take(self, thread_id: str) -> GatewaySession | None:
        """取回并移出该 thread 的 detached 会话（重连续接）；无则 None。"""
        return self._detached.pop(thread_id, None)

    def discard(self, thread_id: str, session: GatewaySession) -> None:
        """移除——仅当登记的就是该 session（避免误删重连后另起的新登记 / TTL 竞态）。"""
        if self._detached.get(thread_id) is session:
            self._detached.pop(thread_id, None)


# 进程级单例（与 broadcast.hub 同风格，全 WS 连接共享）
registry = SessionRegistry()

"""GatewaySession 集成测试：拆分后的并发语义安全网。

desktop 是 ws.py 的关键路径却几乎无测试、维护者无法跑 Electron，故本测试用一个
FakeChannel（收集 send 帧）+ 一个最小鸭子类型 FakeBridge（可控产出 BridgeEvent
序列、可模拟中断收尾），不起真实 LangGraph 即可覆盖：握手、流式/非流式分发、
流式互斥、stop 取消收尾、未知方法、后台通知注入。

逐字保全的并发语义（lock 串行化、stop 补发 turn.complete + {stopped:True}、
"已有任务在执行" 文案、resume 经 broker resolve）均在此锁住。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from lumi.gateway.bridge import BridgeEvent, EventKind
from lumi.gateway.broadcast import BroadcastHub
from lumi.gateway.session import GatewaySession


class FakeChannel:
    """收集 send 帧的假传输（Channel）。"""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.frames.append(frame)

    def responses(self) -> list[dict]:
        return [f for f in self.frames if "id" in f]

    def events(self, event_type: str | None = None) -> list[dict]:
        evts = [f for f in self.frames if f.get("method") == "event"]
        if event_type is None:
            return evts
        return [f for f in evts if f["params"]["type"] == event_type]


class FakeBridge:
    """最小鸭子类型 bridge：可控产出 BridgeEvent 序列、可模拟中断收尾。

    只实现 GatewaySession 路径需要的接口；不起真实 LangGraph。
    """

    def __init__(
        self,
        *,
        events: list[BridgeEvent] | None = None,
        notifications: list[str] | None = None,
    ) -> None:
        self.current_thread_id = "t-1"
        self.model_name = "fake-model"
        self.workspace_dir = "/fake/project"  # 项目随会话绑定后 gateway.ready 取它
        self._events = events or []
        self._notifications = list(notifications or [])
        self.closed = False
        self.stream_response_calls: list[dict] = []
        self.resolve_calls: list[tuple] = []
        self.reject_pending_calls = 0
        # 流式开始/被取消的同步原语，方便测试精确编排
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_response(self, content, *, tool_mode="default", **kwargs):
        self.stream_response_calls.append(
            {"content": content, "tool_mode": tool_mode, **kwargs}
        )
        for evt in self._events:
            yield evt

    def resolve_approval(self, approval_id, value) -> bool:
        self.resolve_calls.append((approval_id, value))
        return True

    def reject_pending(self) -> int:
        # 默认无挂起审批（轮在流生成中途）→ 返回 0，stop/切会话回退到硬取消
        self.reject_pending_calls += 1
        return 0

    def switch_thread(self, tid) -> None:
        self.current_thread_id = tid

    async def stream_command(self, name, *, extra_text="", tool_mode="default"):
        for evt in self._events:
            yield evt

    def list_commands(self) -> list[dict]:
        return [{"name": "compact"}]

    def has_notifications(self) -> bool:
        return bool(self._notifications)

    def drain_notification_hint(self, thread_id=None) -> str:
        return self._notifications.pop(0) if self._notifications else ""

    async def close(self) -> None:
        self.closed = True


class BlockingBridge(FakeBridge):
    """流式轮会一直阻塞，直到测试显式释放——用于测互斥与 stop 取消。"""

    async def stream_response(self, content, *, tool_mode="default", **kwargs):
        self.started.set()
        await self.release.wait()
        for evt in self._events:
            yield evt
        # 让函数成为异步生成器
        return
        yield  # pragma: no cover


class ApprovalBlockingBridge(BlockingBridge):
    """模拟轮挂在审批上：reject_pending() 解开阻塞（同 broker reject 让节点续跑到 END），
    使本轮以拒绝干净跑完，而非被硬取消。"""

    def reject_pending(self) -> int:
        self.reject_pending_calls += 1
        if not self.release.is_set():
            self.release.set()
            return 1
        return 0


def _make_session(bridge: FakeBridge) -> tuple[GatewaySession, FakeChannel]:
    channel = FakeChannel()
    session = GatewaySession(bridge, channel, BroadcastHub())
    return session, channel


# workspace 取自进程级 get_workspace_dir()，不强约束具体值，仅断言为字符串
class _AnyWorkspace:
    def __eq__(self, other) -> bool:
        return isinstance(other, str)


ANY_WORKSPACE = _AnyWorkspace()


# -- 1. 握手 --


async def test_start_emits_gateway_ready():
    bridge = FakeBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        ready = channel.events("gateway.ready")
        assert len(ready) == 1
        params = ready[0]["params"]
        assert params["session_id"] == "t-1"
        assert params["payload"] == {"model": "fake-model", "workspace": ANY_WORKSPACE}
    finally:
        await session.aclose()
    assert bridge.closed is True


# -- 2. 非流式 RPC --


async def test_nonstreaming_rpc_returns_result():
    bridge = FakeBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame({"id": 7, "method": "list_commands", "params": {}})
        await _drain(session)
        responses = [f for f in channel.responses() if f["id"] == 7]
        assert responses == [{"id": 7, "result": {"commands": [{"name": "compact"}]}}]
    finally:
        await session.aclose()


# -- 3. 流式 send_message --


async def test_streaming_send_message_pumps_events_then_result():
    events = [
        BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi"),
        BridgeEvent(kind=EventKind.TURN_COMPLETE),
    ]
    bridge = FakeBridge(events=events)
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "hello"}}
        )
        await _drain(session)
        # 事件按序 pump 出
        assert [e["params"]["type"] for e in channel.events()][1:] == [
            "message.delta",
            "turn.complete",
        ]
        # 末尾响应帧
        assert {"id": 1, "result": {"ok": True}} in channel.responses()
        assert bridge.stream_response_calls[0]["content"] == "hello"
    finally:
        await session.aclose()


# -- 4. 流式进行中再来流式 → 互斥 --


async def test_concurrent_streaming_is_rejected():
    bridge = BlockingBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "first"}}
        )
        await bridge.started.wait()  # 第一轮已进入阻塞
        await session.handle_frame(
            {"id": 2, "method": "send_message", "params": {"content": "second"}}
        )
        rejected = [f for f in channel.responses() if f["id"] == 2]
        assert rejected == [{"id": 2, "error": {"message": "已有任务在执行"}}]
    finally:
        bridge.release.set()
        await session.aclose()


# -- 5. stop 取消进行中的流式 task --


async def test_stop_cancels_streaming_and_finalizes():
    bridge = BlockingBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await bridge.started.wait()
        await session.handle_frame({"id": 9, "method": "stop", "params": {}})
        await _drain(session)

        # stop 立即回 {stopped:True}
        assert {"id": 9, "result": {"stopped": True}} in channel.responses()
        # stop 先试图以拒绝收尾挂起审批（此处无挂起 → 返回 0 → 回退硬取消）
        assert bridge.reject_pending_calls == 1
        # 被取消的流式轮补发 turn.complete + 自身 {stopped:True}
        assert len(channel.events("turn.complete")) == 1
        assert {"id": 1, "result": {"stopped": True}} in channel.responses()
        # task 已清空
        assert session._run.task is None
    finally:
        bridge.release.set()
        await session.aclose()


# -- 5a2. 挂在审批上点 stop：以拒绝收尾让本轮干净跑完（保留历史），不硬取消 --


async def test_stop_during_approval_rejects_and_completes_cleanly():
    """审批挂起时 stop：reject_pending>0 → 本轮以拒绝跑到 END、正常 turn.complete，
    而非取消（消息因 next 为空不被回退丢弃，达成"和以前一样保留历史"）。"""
    bridge = ApprovalBlockingBridge(events=[BridgeEvent(kind=EventKind.TURN_COMPLETE)])
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "rm -rf logs"}}
        )
        await bridge.started.wait()  # 轮进入"审批"阻塞
        await session.handle_frame({"id": 5, "method": "stop", "params": {}})
        await _drain(session)

        # stop 经拒绝收尾（reject_pending 命中）→ 不硬取消
        assert bridge.reject_pending_calls == 1
        # 本轮干净完成（{ok:True}），而非取消的 {stopped:True}
        assert {"id": 1, "result": {"ok": True}} in channel.responses()
        assert {"id": 5, "result": {"stopped": True}} in channel.responses()
        # 本轮自身的 turn.complete 正常 pump 出（非取消补发）
        assert len(channel.events("turn.complete")) == 1
    finally:
        bridge.release.set()
        await session.aclose()


# -- 5b. switch_session 取消挂起轮（审批亮着时切走 = 放弃挂起审批）--


async def test_switch_session_cancels_active_turn():
    """切会话先取消活跃轮（cancel_pending + 取消 task 并等其释放锁），再切 thread。

    否则若该轮正挂在审批上持着 run.lock，switch 会在 async with lock 上死等。
    """
    bridge = BlockingBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await bridge.started.wait()  # 第一轮已进入阻塞（模拟挂在审批上）
        await session.handle_frame(
            {"id": 2, "method": "switch_session", "params": {"thread_id": "t-2"}}
        )
        await _drain(session)

        # 切换成功返回；先试图以拒绝收尾挂起审批；被取消轮补发 turn.complete
        assert {"id": 2, "result": {"thread_id": "t-2"}} in channel.responses()
        assert bridge.reject_pending_calls >= 1
        assert len(channel.events("turn.complete")) == 1
        assert bridge.current_thread_id == "t-2"
        assert session._run.task is None
    finally:
        bridge.release.set()
        await session.aclose()


# -- 6. 未知方法 --


async def test_unknown_method_returns_error():
    bridge = FakeBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame({"id": 3, "method": "does_not_exist", "params": {}})
        await _drain(session)
        errors = [f for f in channel.responses() if f["id"] == 3]
        assert len(errors) == 1
        assert "未知方法" in errors[0]["error"]["message"]
    finally:
        await session.aclose()


# -- 7. 通知轮：有通知 → 注入并 pump（审批挂起期间该轮持锁自然挡住，无需旗标）--


async def test_notification_loop_injects_and_pumps():
    """直接测 _notification_loop 的注入路径：有通知 → drain → stream_response(is_meta)。"""
    events = [BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="bg done")]
    bridge = FakeBridge(events=events, notifications=["后台任务已完成"])
    session, channel = _make_session(bridge)

    # 不走真实 sleep 轮询，直接驱动一次注入（语义等价 loop 抢锁后的体）
    async with session._run.lock:
        hint = bridge.drain_notification_hint(bridge.current_thread_id)
        await session._pump(
            bridge.stream_response(hint, tool_mode="default", is_meta=True)
        )

    # 注入作为不可见 meta 轮，事件被 pump 出
    assert [e["params"]["type"] for e in channel.events()] == ["message.delta"]
    # is_meta 标记透传
    assert bridge.stream_response_calls[0]["is_meta"] is True
    assert bridge.stream_response_calls[0]["content"] == "后台任务已完成"
    # 注：完整 _notification_loop（含 NOTIFICATION_POLL_INTERVAL 轮询、与挂起审批轮
    # 持锁的竞争）只能靠真实 desktop 联调验证。


# -- resume：非流式控制 RPC，唤醒挂起的审批 Future --


async def test_resume_resolves_via_broker():
    """resume 改非流式 RPC：调 bridge.resolve_approval(approval_id, value) 唤醒挂起审批。"""
    bridge = FakeBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {
                "id": 5,
                "method": "resume",
                "params": {"approval_id": "a1", "value": {"decision": "approve"}},
            }
        )
        await _drain(session)
        assert bridge.resolve_calls == [("a1", {"decision": "approve"})]
        assert {"id": 5, "result": {"resolved": True}} in channel.responses()
        # resume 是非流式 RPC，不占 run.task
        assert session._run.task is None
    finally:
        await session.aclose()


async def _drain(session: GatewaySession) -> None:
    """等待 session 当前 spawn 的所有 task（流式轮 + RPC task）结束。"""
    pending = [t for t in (session._run.task, *session._rpc_tasks) if t is not None]
    for t in pending:
        with suppress(asyncio.CancelledError, Exception):
            await t

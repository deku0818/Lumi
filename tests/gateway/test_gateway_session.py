"""GatewaySession 集成测试：拆分后的并发语义安全网。

desktop 是 ws.py 的关键路径却几乎无测试、维护者无法跑 Electron，故本测试用一个
FakeChannel（收集 send 帧）+ 一个最小鸭子类型 FakeBridge（可控产出 BridgeEvent
序列、可模拟中断收尾），不起真实 LangGraph 即可覆盖：握手、流式/非流式分发、
流式互斥、stop 取消收尾、未知方法、后台通知注入。

逐字保全的并发语义（lock 串行化、awaiting_resume 时机、stop 补发 turn.complete +
{stopped:True}、"已有任务在执行" 文案）均在此锁住。
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
        self._events = events or []
        self._notifications = list(notifications or [])
        self.closed = False
        self.stream_response_calls: list[dict] = []
        # 流式开始/被取消的同步原语，方便测试精确编排
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_response(self, content, *, tool_mode="default", **kwargs):
        self.stream_response_calls.append(
            {"content": content, "tool_mode": tool_mode, **kwargs}
        )
        for evt in self._events:
            yield evt

    async def stream_resume(self, value):
        for evt in self._events:
            yield evt

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
        # 非中断收尾 → 不等待 resume
        assert session._run.awaiting_resume is False
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
        session._run.awaiting_resume = True  # 验证取消处理会复位
        await session.handle_frame({"id": 9, "method": "stop", "params": {}})
        await _drain(session)

        # stop 立即回 {stopped:True}
        assert {"id": 9, "result": {"stopped": True}} in channel.responses()
        # 被取消的流式轮补发 turn.complete + 自身 {stopped:True}
        assert len(channel.events("turn.complete")) == 1
        assert {"id": 1, "result": {"stopped": True}} in channel.responses()
        # awaiting_resume 被复位
        assert session._run.awaiting_resume is False
        # task 已清空
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


# -- 7. 通知轮：有通知且非 awaiting_resume → 注入并 pump --


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
    # 注：完整 _notification_loop（含 NOTIFICATION_POLL_INTERVAL 轮询、抢锁前后
    # 双重 awaiting_resume 复检、与真实流式轮的竞争）只能靠真实 desktop 联调验证。


# -- 中断收尾更新 awaiting_resume（_INTERRUPT_KINDS）--


async def test_interrupt_ending_sets_awaiting_resume():
    events = [BridgeEvent(kind=EventKind.APPROVAL, data={"tool": "bash"})]
    bridge = FakeBridge(events=events)
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "run"}}
        )
        await _drain(session)
        assert session._run.awaiting_resume is True
    finally:
        await session.aclose()


async def _drain(session: GatewaySession) -> None:
    """等待 session 当前 spawn 的所有 task（流式轮 + RPC task）结束。"""
    pending = [t for t in (session._run.task, *session._rpc_tasks) if t is not None]
    for t in pending:
        with suppress(asyncio.CancelledError, Exception):
            await t

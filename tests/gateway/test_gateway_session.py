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
import time
from contextlib import suppress
from types import SimpleNamespace

import pytest
from conftest import catalog_entry

from lumi.gateway.bridge import BridgeEvent, EventKind
from lumi.gateway.broadcast import BroadcastHub
from lumi.gateway.session import GatewaySession, _snapshot_model_window


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


def _result(channel, req_id: int) -> dict:
    """取某次 RPC 的 result（按 id）。断子集而非全等：switch_session 等结果会随
    协议增补字段，全等断言会把每次加字段都变成一片红。"""
    return next(r["result"] for r in channel.responses() if r["id"] == req_id)


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
        self.aligned: list[bool] = []  # align_session_model 的 pin 参数记录
        self.workspace_dir = "/fake/project"  # 项目随会话绑定后 gateway.ready 取它
        self.workspace_bound = True  # 已绑定项目：send_message/run_command 关卡放行
        self.mcp_pool_key = lambda: "/fake/project"  # mcp.status 按连接过滤的匹配键
        self.mcp_status_payload = lambda: None  # 无已完成的池加载：注册后不补发
        # 轮首模型对齐：真 bridge 会读 session_meta，这里只记录被调用过
        self.align_session_model = lambda *, pin=False: self.aligned.append(pin)
        self._events = events or []
        self._notifications = list(notifications or [])
        self.closed = False
        self.stream_response_calls: list[dict] = []
        self.resolve_calls: list[tuple] = []
        self.reject_pending_calls = 0
        self.finalize_calls = 0
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

    async def finalize_cancelled_stream(self, gen) -> None:
        # 硬取消收尾时 session 会调用（真 bridge：关图 + 半截回复写回）
        self.finalize_calls += 1

    def pending_approval_events(self) -> list:
        # 断连续接重发用；默认空，测试可注入 _pending_events
        return list(getattr(self, "_pending_events", []))

    def switch_thread(self, tid) -> None:
        self.current_thread_id = tid

    async def recorded_workspace_dir(self, thread_id: str) -> str:
        # 线程 checkpoint 记录的项目；测试可注入 _recorded_workspace 覆盖
        return getattr(self, "_recorded_workspace", "")

    def mark_workspace_bound(self) -> None:
        self.workspace_bound = True

    def mark_workspace_unbound(self) -> None:
        self.workspace_bound = False

    async def stream_command(self, name, *, extra_text="", tool_mode="default"):
        for evt in self._events:
            yield evt

    def list_commands(self) -> list[dict]:
        return [{"name": "compact"}]

    def has_notifications(self, thread_id: str) -> bool:
        return bool(self._notifications)

    def drain_notification_hint(self, thread_id: str) -> str:
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
        assert params["payload"] == {
            "model": "fake-model",
            "workspace": ANY_WORKSPACE,
            "workspace_bound": True,
            "running": False,  # start 时无活跃轮
            "run_started_at": None,
        }
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


async def test_turn_start_carries_user_message_id():
    """开轮事件带出本轮用户消息 id（前端据此给乐观气泡上锚做时间旅行对账）。"""
    bridge = FakeBridge(
        events=[
            BridgeEvent(kind=EventKind.TURN_START, message_id="mid-42"),
            BridgeEvent(kind=EventKind.TURN_COMPLETE),
        ]
    )
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "hello"}}
        )
        await _drain(session)
        start = next(e for e in channel.events() if e["params"]["type"] == "turn.start")
        assert start["params"]["payload"] == {"message_id": "mid-42"}
    finally:
        await session.aclose()


async def test_send_message_rejected_when_workspace_unbound():
    bridge = FakeBridge()
    bridge.workspace_bound = False
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "hello"}}
        )
        assert channel.responses() == [
            {"id": 1, "error": {"message": "请先选择项目再开始对话"}}
        ]
        assert bridge.stream_response_calls == []
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
        # 取消→图收尾+半截写回的接线确实被调用
        assert bridge.finalize_calls == 1
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
        assert _result(channel, 2)["thread_id"] == "t-2"
        assert bridge.reject_pending_calls >= 1
        assert len(channel.events("turn.complete")) == 1
        assert bridge.current_thread_id == "t-2"
        assert session._run.task is None
    finally:
        bridge.release.set()
        await session.aclose()


# -- 5c. switch_session 同 thread（desktop 切回本会话）：不收尾、不动挂起轮 --


async def test_switch_session_same_thread_preserves_active_turn():
    """切回本会话（同 thread）：绝不收尾活跃/挂起轮，审批与运行轮原样保留。

    desktop 每会话一条独立连接，切回只是对同一连接重发「同 thread」的 switch；若误把它当
    「切走」去 reject_pending + 取消，正挂着的审批就被弄丢了（「切走再切回审批还在」不成立）。
    """
    bridge = BlockingBridge()  # 流式轮阻塞 = 模拟挂在审批上
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await bridge.started.wait()  # 轮已进入阻塞（挂在审批上）
        await session.handle_frame(
            {"id": 2, "method": "switch_session", "params": {"thread_id": "t-1"}}
        )
        # 只等 RPC task（被保留的流式轮仍阻塞，不能 _drain 它）
        for tk in list(session._rpc_tasks):
            with suppress(asyncio.CancelledError, Exception):
                await tk

        # 同 thread 立即返回，既不收尾也不取消挂起轮
        assert _result(channel, 2)["thread_id"] == "t-1"
        assert bridge.reject_pending_calls == 0  # 未试图收尾
        assert len(channel.events("turn.complete")) == 0  # 轮未结束
        assert session._run.task is not None and not session._run.task.done()
        assert bridge.current_thread_id == "t-1"
    finally:
        bridge.release.set()
        await session.aclose()


# -- 5d. switch_session 恢复线程项目（cron 执行线程续聊）--


async def test_switch_session_recovers_workspace_from_thread():
    """打开无 workspace 的既有线程（cron 执行线程）时，从其 checkpoint 记录恢复项目绑定。

    前端对 cron 线程 activate(tid, '', backend) 不带 workspace；不恢复则切到新 thread 后
    mark_workspace_unbound，工作区边界关卡拒发「请先选择项目」。
    """
    bridge = FakeBridge()
    bridge.workspace_bound = False  # 起始未绑定，凸显恢复效果
    bridge._recorded_workspace = "/fake/project"  # 线程记录项目 == 引擎当前目录
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "switch_session", "params": {"thread_id": "cron-x"}}
        )
        await _drain(session)
        assert _result(channel, 1)["thread_id"] == "cron-x"
        assert bridge.workspace_bound is True
    finally:
        await session.aclose()


async def test_switch_session_no_recorded_workspace_stays_unbound():
    """线程无记录项目时，切换后保持未绑定（不误绑）。"""
    bridge = FakeBridge()
    bridge._recorded_workspace = ""
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "switch_session", "params": {"thread_id": "t-new"}}
        )
        await _drain(session)
        assert bridge.workspace_bound is False
    finally:
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
    """直接测 _notification_loop 的注入路径：有通知 → drain → stream_response(synthetic)。"""
    events = [BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="bg done")]
    bridge = FakeBridge(events=events, notifications=["后台任务已完成"])
    session, channel = _make_session(bridge)

    # 不走真实 sleep 轮询，直接驱动一次注入（语义等价 loop 抢锁后的体）
    async with session._run.lock:
        hint = bridge.drain_notification_hint(bridge.current_thread_id)
        await session._pump(
            bridge.stream_response(hint, tool_mode="default", synthetic=True)
        )

    # 注入作为不可见合成轮，事件被 pump 出
    assert [e["params"]["type"] for e in channel.events()] == ["message.delta"]
    # synthetic 标记透传
    assert bridge.stream_response_calls[0]["synthetic"] is True
    assert bridge.stream_response_calls[0]["content"] == "后台任务已完成"
    # 注：完整 _notification_loop（含 NOTIFICATION_POLL_INTERVAL 轮询、与挂起审批轮
    # 持锁的竞争）只能靠真实 desktop 联调验证。


async def test_notification_loop_cancel_keeps_parked_turn(monkeypatch):
    """取消通知循环自身（detach 停循环 / aclose 收尾）不代杀正在跑的合成轮——
    detach 契约要求 run task 与挂起 Future 原样存活，句柄保留供 aclose 统一取消。"""
    import lumi.gateway.session as session_mod

    monkeypatch.setattr(session_mod, "NOTIFICATION_POLL_INTERVAL", 0.01)
    bridge = BlockingBridge(notifications=["后台任务已完成"])
    session, _channel = _make_session(bridge)

    notif = asyncio.create_task(session._notification_loop())
    await bridge.started.wait()  # 合成轮已在流中挂住
    pump = session._run.task
    assert pump is not None and not pump.done()

    notif.cancel()
    with suppress(asyncio.CancelledError):
        await notif

    # 合成轮未被代杀、句柄仍在（供 detach 续接 / aclose 统一取消）
    assert session._run.task is pump
    assert not pump.done()

    pump.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await pump


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


# -- 8. 断连续接（Case 1）：detach 保留挂起轮、reattach 重发审批、TTL 兜底 --


def test_session_registry_add_take_discard():
    """registry 语义：add 顶替返回旧的；take 取出即移除；discard 只删登记的那个。"""
    from lumi.gateway.session_registry import SessionRegistry

    reg = SessionRegistry()
    s1, s2 = object(), object()
    assert reg.add("t", s1) is None
    assert reg.add("t", s2) is s1  # 顶替返回旧的
    assert reg.take("t") is s2
    assert reg.take("t") is None
    reg.add("t", s1)
    reg.discard("t", s2)  # 非登记者 → 不删
    assert reg.take("t") is s1


async def test_detach_keeps_parked_turn_and_registers():
    """断开时仍有活跃轮 → detach（不 aclose）：bridge 未关、run.task 存活、登记进 registry。"""
    from lumi.gateway.session_registry import SessionRegistry

    reg = SessionRegistry()
    bridge = BlockingBridge()  # 流式轮阻塞 = 模拟挂在审批上
    session, _ = _make_session(bridge)
    await session.start()
    await session.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await bridge.started.wait()
    try:
        assert session.has_active_turn() is True
        assert session.detach(reg) is None
        assert reg.take("t-1") is session  # 已登记
        assert bridge.closed is False  # 未关
        assert session._run.task is not None and not session._run.task.done()
        assert session._notif_task is None  # 通知轮已停（无 WS 期不推送）
    finally:
        bridge.release.set()
        await session.aclose()


async def test_should_detach_excludes_pure_synthetic_turn():
    """纯后台合成轮断连不续接（无用户在等），除非它自身正挂着审批。"""
    bridge = BlockingBridge()
    session, _ = _make_session(bridge)
    await session.start()
    await session.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await bridge.started.wait()
    try:
        assert session.should_detach() is True  # 普通用户轮 → 续接
        session._run.synthetic = True
        assert session.should_detach() is False  # 纯合成轮 → 不续接
        bridge._pending_events = [
            BridgeEvent(kind=EventKind.APPROVAL, data={"approval_id": "a"})
        ]
        assert session.should_detach() is True  # 合成轮但自身挂着审批 → 仍续接
    finally:
        bridge.release.set()
        await session.aclose()


async def test_reattach_resends_ready_and_pending_approvals():
    """重连续接：新 channel 收到 gateway.ready + 重发的挂起审批卡，TTL 被取消。"""
    from lumi.gateway.session_registry import SessionRegistry

    reg = SessionRegistry()
    approval = BridgeEvent(
        kind=EventKind.APPROVAL, data={"approval_id": "ap-1", "tool_calls": []}
    )
    bridge = BlockingBridge()
    bridge._pending_events = [approval]
    session, _ = _make_session(bridge)
    await session.start()
    before = int(time.time() * 1000)
    await session.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await bridge.started.wait()
    session.detach(reg)
    try:
        ch2 = FakeChannel()
        await session.reattach(ch2)
        ready = ch2.events("gateway.ready")
        assert len(ready) == 1
        # 续接带回本轮开始时刻：前端据此续算计时而非从 0 起跑
        payload = ready[0]["params"]["payload"]
        assert payload["running"] is True
        assert before <= payload["run_started_at"] <= int(time.time() * 1000)
        approvals = ch2.events("approval.request")
        assert len(approvals) == 1
        assert approvals[0]["params"]["payload"]["approval_id"] == "ap-1"
        assert session._ttl_task is None  # TTL 已取消
    finally:
        bridge.release.set()
        await session.aclose()


async def test_detach_ttl_reclaims(monkeypatch):
    """detached 后无人接回到 TTL → 自动回收（registry 移除 + aclose）。"""
    import lumi.gateway.session as session_mod
    from lumi.gateway.session_registry import SessionRegistry

    monkeypatch.setattr(session_mod, "_DETACH_TTL_SECONDS", 0.02)
    reg = SessionRegistry()
    bridge = BlockingBridge()
    session, _ = _make_session(bridge)
    await session.start()
    await session.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await bridge.started.wait()
    session.detach(reg)  # 登记 + 挂 0.02s TTL
    bridge.release.set()
    await asyncio.sleep(0.06)  # 等过 TTL
    assert bridge.closed is True  # 已回收 aclose
    assert reg.take("t-1") is None  # 已从表移除


async def _drain(session: GatewaySession) -> None:
    """等待 session 当前 spawn 的所有 task（流式轮 + RPC task）结束。"""
    pending = [t for t in (session._run.task, *session._rpc_tasks) if t is not None]
    for t in pending:
        with suppress(asyncio.CancelledError, Exception):
            await t


# ── _snapshot_model_window：渠道旁观会话上下文环的分母来源 ──────────────


def _msg(model: str | None):
    """构造带/不带 model_name 标记的鸭子类型消息。"""
    meta = {"model_name": model} if model is not None else {}
    return SimpleNamespace(response_metadata=meta)


def test_snapshot_model_window_takes_last_labeled_model(monkeypatch):
    """回溯取末条带 model_name 的消息，并经 catalog 得其窗口。"""
    monkeypatch.setattr(
        "lumi.models.catalog.lookup",
        lambda name: (
            catalog_entry(context_length=1_000_000) if name == "qwen3.7-plus" else None
        ),
    )
    messages = [_msg("old-model"), _msg("qwen3.7-plus"), _msg(None)]
    assert _snapshot_model_window(messages, "t1") == ("qwen3.7-plus", 1_000_000)


def test_snapshot_model_window_unknown_model_yields_zero(monkeypatch):
    """非渠道会话模型不在目录（catalog 返回 None）→ 窗口 0，前端据此隐藏环。"""
    monkeypatch.setattr("lumi.models.catalog.lookup", lambda name: None)
    assert _snapshot_model_window([_msg("mystery-llm")], "t1") == ("mystery-llm", 0)


def test_snapshot_model_window_no_labeled_message():
    """无任何带 model_name 的消息 → ("", 0)，不触碰 catalog。"""
    assert _snapshot_model_window([_msg(None), _msg(None)], "t1") == ("", 0)
    assert _snapshot_model_window([], "t1") == ("", 0)


def test_snapshot_model_window_channel_falls_back_to_session_model(monkeypatch):
    """渠道会话 wire 名查不到目录（如 LiteLLM 回传 Bedrock ARN）→ 回退该会话生效模型再查。"""
    monkeypatch.setattr(
        "lumi.models.catalog.lookup",
        lambda name: (
            catalog_entry(context_length=1_000_000) if name == "jv-claude" else None
        ),
    )
    monkeypatch.setattr(
        "lumi.sessions.session_model.resolve",
        lambda tid: SimpleNamespace(model="jv-claude", provider="p1"),
    )
    messages = [
        _msg("converse/arn:aws:bedrock:us-east-1:1:application-inference-profile/x")
    ]
    assert _snapshot_model_window(messages, "feishu-oc-1") == ("jv-claude", 1_000_000)


def test_snapshot_model_window_channel_follows_default(monkeypatch):
    """会话没设过模型 → session_model.resolve 落到新会话默认，用它的模型名查窗口。"""
    monkeypatch.setattr(
        "lumi.models.catalog.lookup",
        lambda name: (
            catalog_entry(context_length=128_000) if name == "active-model" else None
        ),
    )
    # resolve 现在既解析模型名也带出限制：无参 = 默认模型，带参 = 查该模型的窗口
    monkeypatch.setattr(
        "lumi.models.provider_store.resolve",
        lambda name=None, provider="": SimpleNamespace(
            model=name or "active-model",
            provider="pg",
            effort="auto",
            context_window=128_000 if (name or "active-model") == "active-model" else 0,
        ),
    )
    assert _snapshot_model_window([_msg("arn:opaque")], "feishu-oc-1") == (
        "active-model",
        128_000,
    )


async def test_detach_drops_cron_observer_and_reattach_restores_it():
    """cron 直播观测者随 channel 走：detach 注销死 channel（否则 drain task 对着死连接
    空转到 TTL），reattach 给新 channel 重新登记（否则续接后直播断流）。"""
    from lumi.gateway.session_registry import SessionRegistry

    reg = SessionRegistry()
    bridge = BlockingBridge()
    bridge.current_thread_id = "cron-abc"
    session, ch1 = _make_session(bridge)
    await session.start()
    session._hub.add_observer(
        "cron-abc", ch1
    )  # 切到 cron 线程时登记（_switch_session）
    await session.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await bridge.started.wait()
    try:
        session.detach(reg)
        assert session._hub.has_observers("cron-abc") is False
        ch2 = FakeChannel()
        await session.reattach(ch2)
        assert list(session._hub._observers["cron-abc"]) == [ch2]
    finally:
        bridge.release.set()
        await session.aclose()


class GateBridge(FakeBridge):
    """每次 stream_response 各挂一道闸（running[i] = (所在 task, 闸)），测试逐个放行。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.running: list[tuple[asyncio.Task, asyncio.Event]] = []

    async def stream_response(self, content, *, tool_mode="default", **kwargs):
        gate = asyncio.Event()
        self.running.append((asyncio.current_task(), gate))
        await gate.wait()
        return
        yield  # pragma: no cover


async def _until(cond) -> None:
    for _ in range(200):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("条件未达成")


async def test_detached_synthetic_turn_keeps_run_lock(monkeypatch):
    """detach 停通知轮时，挂着的合成轮仍持 run.lock——否则重连后同一 bridge 能并发第二轮。"""
    import lumi.gateway.session as session_mod
    from lumi.gateway.session_registry import SessionRegistry

    monkeypatch.setattr(session_mod, "NOTIFICATION_POLL_INTERVAL", 0.01)
    bridge = GateBridge(notifications=["后台任务已完成"])
    session, _ = _make_session(bridge)
    await session.start()
    try:
        await _until(lambda: bridge.running)
        session.detach(SessionRegistry())
        await asyncio.sleep(0.02)
        assert session._run.lock.locked()
    finally:
        await session.aclose()


async def test_notification_turn_never_orphans_queued_user_turn(monkeypatch):
    """跑着的每一轮都必须挂在 _run.task 上（stop / 忙检查 / aclose 都靠它够到）。"""
    import lumi.gateway.session as session_mod

    monkeypatch.setattr(session_mod, "NOTIFICATION_POLL_INTERVAL", 0.01)
    bridge = GateBridge(notifications=["后台任务已完成"])
    session, _ = _make_session(bridge)
    await session.start()
    try:
        async with session._run.lock:  # 非轮操作（如 switch_session）持锁 await
            await asyncio.sleep(0.05)  # 通知轮先排上
            await session.handle_frame(
                {"id": 1, "method": "send_message", "params": {"content": "x"}}
            )
        await _until(lambda: bridge.running)
        bridge.running[0][1].set()
        await asyncio.sleep(0.05)
        for task, _gate in bridge.running:
            if not task.done():
                assert task is session._run.task
    finally:
        for _task, gate in bridge.running:
            gate.set()
        await session.aclose()


async def test_synthetic_flag_resets_after_detached_synthetic_turn(monkeypatch):
    """断连期间合成轮跑完后，之后的真人轮断连仍应续接（synthetic 标记不残留）。"""
    import lumi.gateway.session as session_mod
    from lumi.gateway.session_registry import SessionRegistry

    monkeypatch.setattr(session_mod, "NOTIFICATION_POLL_INTERVAL", 0.01)
    bridge = GateBridge(notifications=["后台任务已完成"])
    session, _ = _make_session(bridge)
    await session.start()
    try:
        await _until(lambda: bridge.running)
        session.detach(SessionRegistry())
        bridge.running[0][1].set()
        await _until(lambda: not session.has_active_turn())
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await _until(lambda: len(bridge.running) == 2)
        assert session.should_detach() is True
    finally:
        for _task, gate in bridge.running:
            gate.set()
        await session.aclose()


async def test_list_commands_for_project_home(isolated_config, tmp_path):
    """项目主页（尚无会话）经控制连接按目标项目列命令：控制连接自己的 bridge 不在该项目。"""
    from lumi.gateway.session import _list_commands

    skill = tmp_path / ".lumi" / "skills" / "beta-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: beta-skill\ndescription: b\n---\nbody")
    session = SimpleNamespace(_bridge=SimpleNamespace(list_commands=lambda: []))
    r = await _list_commands(session, {"workspace": str(tmp_path)})
    assert "beta-skill" in [c["name"] for c in r["commands"]]


# -- 删除会话：先停在途轮再删（否则该轮续写 checkpoint，已删会话「复活」）--


class _DeleteLogBridge(BlockingBridge):
    """记录「停轮」与「删 thread」的先后：删除必须排在持有者的在途轮收尾之后。"""

    def __init__(self, log: list[str], tid: str) -> None:
        super().__init__()
        self.current_thread_id = tid
        self._log = log

    async def finalize_cancelled_stream(self, gen) -> None:
        await super().finalize_cancelled_stream(gen)
        self._log.append("stop")

    async def delete_thread(self, thread_id: str) -> None:
        self._log.append(f"delete:{thread_id}")


def _no_bg_tasks(monkeypatch) -> list[str]:
    import lumi.gateway.session as session_mod

    cancelled: list[str] = []

    async def fake_cancel(thread_id: str) -> int:
        cancelled.append(thread_id)
        return 0

    monkeypatch.setattr(session_mod, "cancel_thread_bg_tasks", fake_cancel)
    return cancelled


async def test_delete_session_stops_turn_on_other_connection_first(monkeypatch):
    _no_bg_tasks(monkeypatch)
    log: list[str] = []
    owner_bridge = _DeleteLogBridge(log, "t-del-live")
    owner, _ = _make_session(owner_bridge)
    ctl, ctl_ch = _make_session(_DeleteLogBridge(log, "t-ctl"))
    await owner.start()
    await ctl.start()
    try:
        await owner.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await owner_bridge.started.wait()
        await ctl.handle_frame(
            {"id": 2, "method": "delete_session", "params": {"thread_id": "t-del-live"}}
        )
        await _drain(ctl)
        assert _result(ctl_ch, 2) == {"thread_id": "t-del-live"}
        assert log == ["stop", "delete:t-del-live"]
        assert owner.has_active_turn() is False
    finally:
        owner_bridge.release.set()
        await owner.aclose()
        await ctl.aclose()


async def test_delete_session_closes_detached_owner_and_its_leftovers(monkeypatch):
    from lumi.agents.runtime.bg_tasks import get_task_registry
    from lumi.gateway.session_registry import registry

    cancelled = _no_bg_tasks(monkeypatch)
    queue = get_task_registry().notification_queue
    log: list[str] = []
    owner_bridge = _DeleteLogBridge(log, "t-del-detached")
    owner, _ = _make_session(owner_bridge)
    ctl, _ = _make_session(_DeleteLogBridge(log, "t-ctl"))
    await owner.start()
    await ctl.start()
    await owner.handle_frame(
        {"id": 1, "method": "send_message", "params": {"content": "x"}}
    )
    await owner_bridge.started.wait()
    owner.detach(registry)  # 前端关了该会话的连接，轮挂在 registry 里续跑
    queue.enqueue("<task-notification/>", "t-del-detached")
    try:
        await ctl.handle_frame(
            {
                "id": 2,
                "method": "delete_session",
                "params": {"thread_id": "t-del-detached"},
            }
        )
        await _drain(ctl)
        assert log == ["stop", "delete:t-del-detached"]
        assert owner_bridge.closed is True
        assert registry.take("t-del-detached") is None
        assert cancelled == ["t-del-detached"]
        assert queue.has_for("t-del-detached") is False
    finally:
        owner_bridge.release.set()
        registry.discard("t-del-detached", owner)
        await owner.aclose()
        await ctl.aclose()


@pytest.mark.parametrize("during_init", [False, True])
async def test_delete_session_cancels_cron_and_queued_desktop_turn(
    monkeypatch, during_init
):
    from lumi.gateway.cron_stream import build_cron_stream_runner

    _no_bg_tasks(monkeypatch)
    tid = "cron-delete-shared"
    log = []
    entered = asyncio.Event()

    class CronBridge:
        async def initialize(self, **kwargs):
            if during_init:
                entered.set()
                await asyncio.Event().wait()

        def switch_thread(self, thread_id):
            pass

        async def stream_response(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover

        async def finalize_cancelled_stream(self, stream):
            await stream.aclose()
            await asyncio.sleep(0)  # checkpoint 写回确实发生 await。
            log.append("finalize")

        async def close(self):
            log.append("close")

    monkeypatch.setattr("lumi.gateway.bridge.AgentBridge", CronBridge)
    waiting_bridge = BlockingBridge()
    waiting_bridge.current_thread_id = tid
    waiting, _ = _make_session(waiting_bridge)
    ctl, ctl_ch = _make_session(_DeleteLogBridge(log, "t-ctl"))
    await waiting.start()
    await ctl.start()
    cron = asyncio.create_task(build_cron_stream_runner(BroadcastHub())("job", tid, ""))
    try:
        await entered.wait()
        await waiting.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "queued"}}
        )
        await asyncio.sleep(0)
        await ctl.handle_frame(
            {"id": 2, "method": "delete_session", "params": {"thread_id": tid}}
        )
        await asyncio.wait_for(_drain(ctl), 1)
        await _drain(waiting)
        assert _result(ctl_ch, 2) == {"thread_id": tid}
        expected = ["close", f"delete:{tid}"]
        assert log == (expected if during_init else ["finalize", *expected])
        assert not waiting_bridge.started.is_set()
        with pytest.raises(asyncio.CancelledError):
            await cron
    finally:
        cron.cancel()
        await asyncio.gather(cron, return_exceptions=True)
        await waiting.aclose()
        await ctl.aclose()


# -- provider 写类 RPC 改的是机器级配置，不等本会话在途轮 --


async def test_provider_writes_do_not_wait_for_running_turn(monkeypatch):
    import lumi.gateway.session as session_mod

    methods = (
        "set_provider",
        "save_provider",
        "delete_provider",
        "set_effort",
        "set_classifier",
        "set_titler",
    )
    for m in methods:
        monkeypatch.setattr(session_mod.providers, m, lambda *a: {"ok": True})
    bridge = BlockingBridge()
    session, channel = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "send_message", "params": {"content": "x"}}
        )
        await bridge.started.wait()
        for i, m in enumerate(methods, start=10):
            await session.handle_frame({"id": i, "method": m, "params": {}})
        await asyncio.wait_for(asyncio.gather(*session._rpc_tasks), timeout=1)
        for i in range(10, 10 + len(methods)):
            assert _result(channel, i) == {"ok": True}
    finally:
        bridge.release.set()
        await session.aclose()


# -- 项目「最近使用」随会话绑定刷新；list_projects 不再下发进程级 current --


async def test_switch_session_touches_bound_project(monkeypatch):
    import lumi.gateway.session as session_mod

    touched: list[str] = []
    monkeypatch.setattr(session_mod, "touch_project", touched.append)
    bridge = FakeBridge()
    session, _ = _make_session(bridge)
    await session.start()
    try:
        await session.handle_frame(
            {"id": 1, "method": "switch_session", "params": {"thread_id": "t-2"}}
        )
        await _drain(session)
        assert touched == []  # 未绑定项目的切换不算使用
        await session.handle_frame(
            {
                "id": 2,
                "method": "switch_session",
                "params": {"thread_id": "t-3", "workspace": "/fake/project"},
            }
        )
        await _drain(session)
        assert touched == ["/fake/project"]
    finally:
        await session.aclose()


async def test_list_projects_has_no_process_current(isolated_config):
    from lumi.gateway.session import _list_projects

    session = SimpleNamespace(_bridge=SimpleNamespace(workspace_dir="/cwd"))
    assert "current" not in await _list_projects(session, {})


class _OpenWs:
    """ws_endpoint 的最小假 WebSocket：握手后立即断开。"""

    def __init__(self, query: dict) -> None:
        self.query_params = query

    async def accept(self) -> None:
        return

    async def send_json(self, frame: dict) -> None:
        return

    async def receive_json(self) -> dict:
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect()


async def test_ws_open_with_bound_workspace_touches_project(monkeypatch):
    from lumi.gateway.channels import ws as ws_mod

    class OpenBridge(FakeBridge):
        async def initialize(self, project_dir: str = "") -> None:
            self.workspace_dir = project_dir or "/cwd"
            self.workspace_bound = bool(project_dir)

    touched: list[str] = []
    monkeypatch.setattr(ws_mod, "touch_project", touched.append)
    monkeypatch.setattr(ws_mod, "AgentBridge", OpenBridge)
    await ws_mod.ws_endpoint(_OpenWs({"workspace": "/p/a"}))
    await ws_mod.ws_endpoint(_OpenWs({}))
    assert touched == ["/p/a"]

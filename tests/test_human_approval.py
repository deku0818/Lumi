"""human_approval 节点测试：经在途审批 Broker 拿 decision 后的三态路由 + DENY 快速拒绝。

只换底层等待机制（interrupt → broker.request），三态路由 / DENY 防御分支语义不变。
"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.graph import END

from lumi.agents.core.nodes import human_approval
from lumi.agents.permissions.models import PermissionDecision


class _FakeBroker:
    """回应预设 decision，并捕获 ask/审批 payload。"""

    def __init__(self, decision):
        self._decision = decision
        self.calls: list[dict] = []

    async def request(self, payload, reject_value):
        self.calls.append(payload)
        return self._decision


class _DenyEngine:
    """evaluate 恒返回 DENY，用于触发节点内防御性快速拒绝。"""

    def evaluate(self, name, args):
        return PermissionDecision.DENY


def _state(tool_calls):
    return {"messages": [AIMessage(content="", tool_calls=tool_calls)]}


def _runtime(decision=None, engine=None):
    return SimpleNamespace(
        context=SimpleNamespace(
            permission_engine=engine, approval_broker=_FakeBroker(decision)
        )
    )


_TCS = [{"id": "tc1", "name": "bash", "args": {"command": "ls"}}]


async def test_approve_routes_to_tool_executor():
    rt = _runtime(decision={"decision": "approve"})
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "ToolExecutor"
    # broker 收到正确 payload
    payload = rt.context.approval_broker.calls[0]
    assert payload["type"] == "tool_approval"
    assert payload["tool_calls"] == [
        {"id": "tc1", "name": "bash", "args": {"command": "ls"}}
    ]


async def test_approve_with_set_tool_mode_updates_state():
    rt = _runtime(decision={"decision": "approve", "set_tool_mode": "privileged"})
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "ToolExecutor"
    assert cmd.update == {"tool_mode": "privileged"}


async def test_reject_routes_to_end_with_message():
    rt = _runtime(decision={"decision": "reject", "message": "不行"})
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == END
    msg = cmd.update["messages"][0]
    assert msg.tool_call_id == "tc1"
    assert "不行" in msg.content


async def test_cancel_routes_to_end():
    rt = _runtime(decision={"decision": "cancel"})
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == END
    assert cmd.update["messages"][0].tool_call_id == "tc1"


async def test_string_decision_falls_to_reject():
    """退化为纯字符串 decision（headless）：非 approve/cancel 走 reject 默认。"""
    rt = _runtime(decision="reject")
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == END


async def test_deny_skips_broker_and_routes_to_call_model():
    """DENY 命中：跳过 broker（不发审批），直接拒绝并路由回 CallModel。"""
    rt = _runtime(decision={"decision": "approve"}, engine=_DenyEngine())
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "CallModel"
    # broker 未被调用
    assert rt.context.approval_broker.calls == []


async def test_no_broker_headless_fails_closed():
    """无审批通道（headless：cron / workflow，approval_broker=None）：fail-closed 拒绝回 CallModel，不崩溃。"""
    rt = SimpleNamespace(
        context=SimpleNamespace(permission_engine=None, approval_broker=None)
    )
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "CallModel"
    assert cmd.update["messages"][0].tool_call_id == "tc1"


async def test_stop_via_reject_keeps_user_message_and_clean_state():
    """端到端：真实图挂在 human_approval 审批上，stop 经 broker.reject_all 收尾——本轮以
    拒绝跑到 END、checkpoint 干净（next 为空，下轮不回退），用户消息保留在历史里。

    锁住用户要的「停止也跟以前一样保留那句话」的核心行为。
    """
    from typing import Annotated, TypedDict

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import START, StateGraph
    from langgraph.graph.message import add_messages

    from lumi.gateway.bridge.broker import LUMI_APPROVAL_EVENT, ApprovalBroker

    broker = ApprovalBroker()

    class _GS(TypedDict):
        messages: Annotated[list, add_messages]

    async def call_model(state):
        return {"messages": [AIMessage(content="", tool_calls=list(_TCS))]}

    async def approval_node(state):
        rt = SimpleNamespace(
            context=SimpleNamespace(permission_engine=None, approval_broker=broker)
        )
        return await human_approval(state, rt)

    g = StateGraph(_GS)
    g.add_node("call_model", call_model)
    g.add_node("HumanApproval", approval_node)
    g.add_node("ToolExecutor", lambda s: {"messages": []})
    g.add_edge(START, "call_model")
    g.add_edge("call_model", "HumanApproval")
    g.add_edge("ToolExecutor", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}

    seen: list[str] = []

    async def turn():
        async for ev in graph.astream_events(
            {"messages": [HumanMessage(content="删掉所有日志")]}, cfg, version="v2"
        ):
            if ev["event"] == "on_custom_event" and ev["name"] == LUMI_APPROVAL_EVENT:
                seen.append("approval")

    task = asyncio.create_task(turn())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if seen:
            break
    assert seen == ["approval"]

    assert broker.reject_all() == 1  # 模拟点"停止"
    await task  # 本轮以拒绝干净跑到 END

    snap = await graph.aget_state(cfg)
    assert snap.next == ()  # 干净：下一轮 _recover_stale_state 不会回退
    assert any(
        isinstance(m, HumanMessage) and "删掉所有日志" in m.content
        for m in snap.values["messages"]
    )  # 用户消息保留

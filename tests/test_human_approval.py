"""human_approval 节点测试：经在途审批 Broker 拿 decision 后的三态路由 + DENY 快速拒绝。"""

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
            permission_engine=engine,
            approval_broker=_FakeBroker(decision),
            widen_boundary=None,  # headless/无 bridge：边界不放宽（见 test_approval_boundary_widen）
            tool_mode="default",
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


async def test_approve_with_set_tool_mode_switches_context():
    rt = _runtime(decision={"decision": "approve", "set_tool_mode": "privileged"})
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "ToolExecutor"
    # set_tool_mode 现在改共享 context（运行时真相源），不再走 Command.update
    assert rt.context.tool_mode == "privileged"


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
        context=SimpleNamespace(
            permission_engine=None, approval_broker=None, widen_boundary=None
        )
    )
    cmd = await human_approval(_state(_TCS), rt)
    assert cmd.goto == "CallModel"
    assert cmd.update["messages"][0].tool_call_id == "tc1"


# === 逐个审批：decisions 与 tool_calls 同序 ===

_TCS3 = [
    {"id": "a", "name": "bash", "args": {"command": "ls"}},
    {"id": "b", "name": "bash", "args": {"command": "rm -rf build"}},
    {"id": "c", "name": "read", "args": {"file_path": "x.md"}},
]


async def test_per_call_all_approve_executes_whole_batch():
    rt = _runtime(decision={"decisions": ["approve"] * 3})
    cmd = await human_approval(_state(_TCS3), rt)
    assert cmd.goto == "ToolExecutor"
    assert not cmd.update


async def test_per_call_all_reject_ends_turn():
    rt = _runtime(decision={"decisions": ["reject"] * 3, "message": "不行"})
    cmd = await human_approval(_state(_TCS3), rt)
    assert cmd.goto == END
    assert [m.tool_call_id for m in cmd.update["messages"]] == ["a", "b", "c"]


async def test_per_call_partial_rejects_only_rejected_then_executes():
    """部分拒绝：只为被拒调用补拒绝 ToolMessage 并进 ToolExecutor（执行完回 CallModel 继续）。"""
    rt = _runtime(
        decision={"decisions": ["approve", "reject", "approve"], "message": "不行"}
    )
    cmd = await human_approval(_state(_TCS3), rt)
    assert cmd.goto == "ToolExecutor"
    msgs = cmd.update["messages"]
    assert [m.tool_call_id for m in msgs] == ["b"]
    assert "不行" in msgs[0].content


async def test_per_call_missing_decision_fails_closed():
    """decisions 短于 tool_calls：缺项按拒绝。"""
    rt = _runtime(decision={"decisions": ["approve"]})
    cmd = await human_approval(_state(_TCS3), rt)
    assert cmd.goto == "ToolExecutor"
    assert [m.tool_call_id for m in cmd.update["messages"]] == ["b", "c"]


async def test_malformed_answer_fails_closed():
    """resume 的 value 由客户端给、形状不可信：非 dict 一律按拒绝收尾。

    回归：曾直接 result.get(...)，客户端漏传 value（None）时以 AttributeError
    打断整条流式——挂起的工具既没执行也没被干净拒掉，本轮就此崩掉。
    """
    for bad in (None, "approve", ["approve"], 42):
        rt = _runtime(decision=bad)
        cmd = await human_approval(_state(_TCS3), rt)
        assert cmd.goto == END, f"{bad!r} 应按拒绝收尾"
        assert len(cmd.update["messages"]) == 3  # 三个调用各补一条拒绝 ToolMessage


async def test_tool_executor_runs_only_unanswered_calls(monkeypatch):
    """部分拒绝后进 ToolExecutor：已有拒绝 ToolMessage 的调用不再执行，只跑剩余的。"""
    from langchain_core.messages import ToolMessage

    from lumi.agents.core import nodes
    from lumi.agents.core.nodes import build_reject_messages, tool_executor
    from lumi.agents.core.state import LumiAgentContext

    executed: list[str] = []

    class _CaptureToolNode:
        def __init__(self, tools, handle_tool_errors=None):
            pass

        async def ainvoke(self, calls, config=None):
            executed.extend(tc["id"] for tc in calls)
            return {
                "messages": [
                    ToolMessage(content="ok", tool_call_id=tc["id"], name=tc["name"])
                    for tc in calls
                ]
            }

    monkeypatch.setattr(nodes, "ToolNode", _CaptureToolNode)
    ai = AIMessage(content="", tool_calls=_TCS3)
    state = {"messages": [ai, *build_reject_messages([_TCS3[1]], content="不行")]}
    result = await tool_executor(
        state, SimpleNamespace(context=LumiAgentContext(tools=[])), {}
    )
    assert executed == ["a", "c"]
    assert [m.tool_call_id for m in result["messages"]] == ["a", "c"]


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

    from lumi.agents.core.broker import LUMI_APPROVAL_EVENT, ApprovalBroker

    broker = ApprovalBroker()

    class _GS(TypedDict):
        messages: Annotated[list, add_messages]

    async def call_model(state):
        return {"messages": [AIMessage(content="", tool_calls=list(_TCS))]}

    async def approval_node(state):
        rt = SimpleNamespace(
            context=SimpleNamespace(
                permission_engine=None, approval_broker=broker, widen_boundary=None
            )
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

"""运行中插话：图在工具边界 / 模型想结束时取走收件箱注入当前轮；压缩时开轮诉求与
插话一并原样保住。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from lumi.agents.core import nodes
from lumi.agents.core.inbox import MAX_PENDING, Inbox, InboxFullError
from lumi.agents.core.meta_message import (
    declared_file_paths,
    declared_items,
    is_interjection,
)
from lumi.agents.core.preprocessing.compact import pending_humans
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.gateway.bridge import AgentBridge, BridgeEvent, EventKind
from lumi.utils.constants import LUMI_META_KEY


def _user(text: str, mid: str) -> HumanMessage:
    return HumanMessage(
        content=text,
        id=mid,
        additional_kwargs={LUMI_META_KEY: {"items": [{"text": text}]}},
    )


class _OkToolNode:
    def __init__(self, tools, handle_tool_errors=None):
        pass

    async def ainvoke(self, calls, config=None):
        return {"messages": [ToolMessage(content="ok", tool_call_id="b", name="bash")]}


def _graph(node_name: str, node):
    builder = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    builder.add_node(node_name, node)
    builder.add_node("CallModel", lambda _s: {})
    builder.add_edge(START, node_name)
    builder.add_edge("CallModel", END)
    if node_name == "ToolExecutor":
        builder.add_edge(node_name, END)
    return builder.compile()


def _ctx(*msgs: HumanMessage, open_: bool = True) -> LumiAgentContext:
    inbox = Inbox()
    for m in msgs:
        inbox.put(m)
    inbox.open = open_
    return LumiAgentContext(inbox=inbox)


_AI_CALL = AIMessage(
    content="", tool_calls=[{"name": "bash", "args": {"command": "ls"}, "id": "b"}]
)


async def test_tool_boundary_injects_after_tool_results(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _OkToolNode)
    ctx = _ctx(_user("别动 config.py", "u2"))
    result = await _graph("ToolExecutor", nodes.tool_executor).ainvoke(
        {"messages": [_user("重构 utils", "u1"), _AI_CALL]}, context=ctx
    )
    *_, tool_msg, injected = result["messages"]
    assert isinstance(tool_msg, ToolMessage)  # tool_result 紧跟 tool_use
    assert injected.id == "u2" and is_interjection(injected)
    assert injected.content == "别动 config.py"  # 原样注入，不加说明
    assert ctx.inbox.pending() == []


async def test_closed_inbox_is_not_drained(monkeypatch):
    """系统轮（收件箱未开放）不注入，消息留到轮结束后另起一轮。"""
    monkeypatch.setattr(nodes, "ToolNode", _OkToolNode)
    ctx = _ctx(_user("顺便看下 lint", "u2"), open_=False)
    result = await _graph("ToolExecutor", nodes.tool_executor).ainvoke(
        {"messages": [_AI_CALL]}, context=ctx
    )
    assert isinstance(result["messages"][-1], ToolMessage)
    assert [m.id for m in ctx.inbox.pending()] == ["u2"]


async def test_stop_pulls_back_with_interjection():
    """模型想结束时仍有插话：注入并拉回 CallModel，不结束本轮。"""
    ctx = _ctx(_user("再补一句", "u2"))
    result = await _graph("OnAgentStop", nodes.on_agent_stop).ainvoke(
        {"messages": [_user("问题", "u1"), AIMessage(content="答完了")]}, context=ctx
    )
    assert result["messages"][-1].id == "u2"
    assert ctx.inbox.pending() == []


def test_pending_humans_keeps_opener_with_interjection():
    opener = _user("重构 utils", "u1")
    interjection = _user("别动 config.py", "u2")
    interjection.additional_kwargs["is_interjection"] = True
    msgs = [
        _user("旧问题", "u0"),
        AIMessage(content="旧回答"),
        opener,
        _AI_CALL,
        ToolMessage(content="ok", tool_call_id="b"),
        interjection,
    ]
    assert pending_humans(msgs) == [opener, interjection]


async def test_bridge_interject_builds_regular_user_message():
    """插话与普通用户消息同构（显示声明 / 预生成 id），回放与时间旅行无需特判。"""
    bridge = AgentBridge()
    mid = await bridge.interject(
        "别动",
        message_id="m1",
        message_meta={"items": [{"sender": "李雷", "text": "别动"}]},
    )
    [msg] = bridge.pending_interjections()
    assert mid == msg.id == "m1"
    assert declared_items(msg)[0]["sender"] == "李雷"


@pytest.mark.parametrize("message_id", ["m1", ""])
async def test_idle_send_lands_client_pending_message(
    monkeypatch: pytest.MonkeyPatch, message_id: str
) -> None:
    """上传期间原轮已结束：普通开轮也要让预生成 id 的托盘消息落地。"""
    bridge = AgentBridge()
    messages: list[HumanMessage] = []

    async def user_turn(
        msg: HumanMessage, tool_mode: str
    ) -> AsyncIterator[BridgeEvent]:
        messages.append(msg)
        yield BridgeEvent(kind=EventKind.TURN_START, message_id=msg.id)
        yield BridgeEvent(kind=EventKind.TURN_COMPLETE)

    monkeypatch.setattr(bridge, "_stream_user_turn", user_turn)
    events = [
        evt
        async for evt in bridge.stream_response(
            "补充", attachments=["/tmp/report.pdf"], message_id=message_id
        )
    ]
    expected_kinds = [EventKind.TURN_START, EventKind.TURN_COMPLETE]
    if message_id:
        expected_kinds.insert(0, EventKind.MESSAGE_INJECTED)
        assert events[0].data == {"message_ids": [message_id]}
        assert messages[0].id == message_id
    assert [evt.kind for evt in events] == expected_kinds
    assert declared_items(messages[0])[0]["text"] == "补充"
    assert declared_file_paths(messages[0]) == ["/tmp/report.pdf"]


async def test_inbox_caps_pending():
    bridge = AgentBridge()
    for i in range(MAX_PENDING):
        await bridge.interject(f"第{i}条")
    with pytest.raises(InboxFullError):
        await bridge.interject("再来一条")


def test_pending_humans_plain_turn_keeps_only_last():
    msgs = [_user("旧问题", "u0"), AIMessage(content="旧回答"), _user("新问题", "u1")]
    assert [m.id for m in pending_humans(msgs)] == ["u1"]

"""真实 ToolExecutor 事件与历史对账：终止/补拉不能复制工具行。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from lumi.agents.core.nodes import tool_executor
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.runtime.subagents.lumi import translate_lumi_event
from lumi.agents.tools.providers.bash import bash
from lumi.gateway.bridge import AgentBridge, EventKind
from lumi.gateway.session import _history_items


def _graph():
    builder = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    builder.add_node("ToolExecutor", tool_executor)
    builder.add_edge(START, "ToolExecutor")
    builder.add_edge("ToolExecutor", END)
    return builder.compile(checkpointer=InMemorySaver())


async def test_timeout_finishes_same_tool_and_survives_history_reload(project_tmp_dir):
    graph = _graph()
    bridge = AgentBridge()
    config = {"configurable": {"thread_id": "tool-timeout"}}
    call = {
        "name": "bash",
        "args": {"command": "sleep 5", "description": "超时回归", "timeout": 0.05},
        "id": "call-timeout",
    }
    events, subagent_events = [], []
    async with asyncio.timeout(5):
        async for event in graph.astream_events(
            {"messages": [AIMessage(content="", tool_calls=[call])]},
            config,
            context=LumiAgentContext(tools=[bash], tool_mode="privileged"),
            version="v2",
        ):
            events.extend(bridge._translate_event(event))
            translate_lumi_event(
                event, lambda kind, payload: subagent_events.append((kind, payload))
            )
    tools = [
        e for e in events if e.kind in (EventKind.TOOL_START, EventKind.TOOL_COMPLETE)
    ]
    assert [e.tool_call_id for e in tools] == [call["id"], call["id"]]
    assert tools[-1].is_error
    assert "Timeout" in tools[-1].output
    assert "0.05" in tools[-1].output
    subagent_tools = [
        payload
        for kind, payload in subagent_events
        if kind in ("tool.start", "tool.complete")
    ]
    assert [event["tool_call_id"] for event in subagent_tools] == [call["id"]] * 2
    assert subagent_tools[0]["args"] == tools[0].args == call["args"]
    assert subagent_tools[-1]["is_error"]
    assert "Timeout" in subagent_tools[-1]["output"]
    messages = (await graph.aget_state(config)).values["messages"]
    assert isinstance(messages[-1], ToolMessage)
    assert messages[-1].status == "error"
    history = _history_items(messages)
    assert len(history) == 1
    assert history[0]["tool_call_id"] == tools[0].tool_call_id
    assert history[0]["is_error"]


async def test_parallel_same_name_tools_keep_ids_when_cancelled():
    started = asyncio.Event()
    count = 0

    @tool
    async def waiting(label: str) -> str:
        """等待取消。"""
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await asyncio.Event().wait()
        return label

    graph = _graph()
    bridge = AgentBridge()
    config = {"configurable": {"thread_id": "tool-cancel"}}
    calls = [
        {"name": "waiting", "args": {"label": str(i)}, "id": f"call-{i}"}
        for i in range(2)
    ]
    events = []

    async def consume():
        async for event in graph.astream_events(
            {"messages": [AIMessage(content="", tool_calls=calls)]},
            config,
            context=LumiAgentContext(tools=[waiting], tool_mode="privileged"),
            version="v2",
        ):
            events.extend(bridge._translate_event(event))

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    starts = [e for e in events if e.kind == EventKind.TOOL_START]
    assert {e.tool_call_id for e in starts} == {c["id"] for c in calls}
    history = _history_items((await graph.aget_state(config)).values["messages"])
    assert {it["tool_call_id"] for it in history} == {e.tool_call_id for e in starts}

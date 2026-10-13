"""真实 ToolExecutor + ToolNode：同批权限、AI 裁决、人工等待互不连带。"""

import asyncio
from types import SimpleNamespace
from typing import Annotated

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from lumi.agents.core import nodes
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.permissions.models import Permission, PermissionConfig, PermissionRule


class WaitingBroker:
    def __init__(self):
        self.requested = asyncio.Event()
        self.answer = asyncio.Future()
        self.calls = []

    async def request(self, payload, reject_value):
        self.calls.append(payload)
        self.requested.set()
        return await self.answer


def setup_run(tmp_path, mode, broker=None, rules=()):
    seen = []
    executed = asyncio.Event()

    @tool
    async def probe(label: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """记录一个测试调用；image 返回图片回灌形态。"""
        seen.append(label)
        executed.set()
        if label == "image":
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="image", tool_call_id=tool_call_id, name="probe"
                        ),
                        HumanMessage(content="image content"),
                    ]
                }
            )
        return label

    engine = PermissionEngine(tmp_path, user_config_dir=tmp_path / "user")
    engine._config = PermissionConfig(
        tuple(
            PermissionRule(f"probe({label})", permission) for label, permission in rules
        )
    )
    context = LumiAgentContext(
        tools=[probe], permission_engine=engine, tool_mode=mode, approval_broker=broker
    )
    builder = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    builder.add_node("ToolExecutor", nodes.tool_executor)
    builder.add_edge(START, "ToolExecutor")
    builder.add_edge("ToolExecutor", END)
    return builder.compile(), context, seen, executed


def state(*labels):
    return {
        "messages": [
            HumanMessage(content="执行这些测试操作"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": label, "name": "probe", "args": {"label": label}}
                    for label in labels
                ],
            ),
        ]
    }


def results(output):
    return [m for m in output["messages"] if isinstance(m, ToolMessage)]


@pytest.mark.parametrize("mode", ["privileged", "auto", "default", "accept_edits"])
async def test_deny_does_not_reject_allow(tmp_path, mode):
    graph, context, seen, _ = setup_run(
        tmp_path,
        mode,
        rules=[("blocked", Permission.DENY), ("allowed", Permission.ALLOW)],
    )
    output = await graph.ainvoke(state("blocked", "allowed"), context=context)
    assert seen == ["allowed"]
    assert [(m.tool_call_id, m.content) for m in results(output)] == [
        ("blocked", "此调用被权限策略拒绝，禁止换工具绕过该限制。"),
        ("allowed", "allowed"),
    ]


async def test_privileged_ask_executes_alongside_allow(tmp_path):
    graph, context, seen, _ = setup_run(
        tmp_path,
        "privileged",
        rules=[("ask", Permission.ASK), ("allowed", Permission.ALLOW)],
    )
    await graph.ainvoke(state("ask", "allowed"), context=context)
    assert set(seen) == {"ask", "allowed"}


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_manual_wait_and_reject_do_not_block_allow(tmp_path, decision):
    broker = WaitingBroker()
    graph, context, seen, executed = setup_run(
        tmp_path,
        "default",
        broker,
        [("ask", Permission.ASK), ("allowed", Permission.ALLOW)],
    )
    task = asyncio.create_task(graph.ainvoke(state("ask", "allowed"), context=context))
    try:
        await asyncio.wait_for(broker.requested.wait(), 2)
        await asyncio.wait_for(executed.wait(), 2)
        assert seen == ["allowed"]
        assert [tc["id"] for tc in broker.calls[0]["tool_calls"]] == ["ask"]
        broker.answer.set_result({"decision": decision})
        output = await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert set(seen) == ({"allowed", "ask"} if decision == "approve" else {"allowed"})
    assert not output.get("tool_cancelled")
    assert [m.tool_call_id for m in results(output)] == ["ask", "allowed"]


@pytest.mark.parametrize("failure", [False, True])
async def test_ai_judges_each_call_and_failure_is_local(tmp_path, monkeypatch, failure):
    calls = []
    gate = asyncio.Event()

    class Classifier:
        async def ainvoke(self, payload):
            rendered = payload["tool_calls"]
            calls.append(rendered)
            assert rendered.count("- probe(") == 1
            if "bad" in rendered:
                await gate.wait()
                if failure:
                    raise ValueError("classifier unavailable")
                return SimpleNamespace(decision="reject", reason="拒绝 bad")
            return SimpleNamespace(decision="approve", reason="允许 good")

    monkeypatch.setattr(nodes, "structured_output", lambda **kw: Classifier())
    monkeypatch.setattr(
        nodes,
        "resolve_pointer",
        lambda *a: SimpleNamespace(model="fake", conn_kwargs=lambda: {}),
    )
    graph, context, seen, executed = setup_run(
        tmp_path,
        "auto",
        rules=[
            ("bad", Permission.ASK),
            ("good", Permission.ASK),
            ("allowed", Permission.ALLOW),
        ],
    )
    task = asyncio.create_task(
        graph.ainvoke(state("bad", "good", "allowed"), context=context)
    )
    try:
        await asyncio.wait_for(executed.wait(), 2)
        assert "bad" not in seen
        gate.set()
        output = await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert set(seen) == {"good", "allowed"}
    assert len(calls) == 2  # ALLOW 不交分类器
    assert [m.tool_call_id for m in results(output)] == ["bad", "good", "allowed"]
    assert not output.get("tool_cancelled")


async def test_denied_result_and_allowed_media_keep_protocol_order(tmp_path):
    graph, context, seen, _ = setup_run(
        tmp_path, "privileged", rules=[("blocked", Permission.DENY)]
    )
    output = await graph.ainvoke(state("image", "blocked", "allowed"), context=context)
    assert set(seen) == {"image", "allowed"}
    assert [type(m).__name__ for m in output["messages"][2:]] == [
        "ToolMessage",
        "ToolMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert [m.tool_call_id for m in results(output)] == ["image", "blocked", "allowed"]


async def test_explicit_stop_halts_even_after_allowed_sibling_executed(tmp_path):
    broker = WaitingBroker()
    graph, context, seen, executed = setup_run(
        tmp_path,
        "default",
        broker,
        [("ask", Permission.ASK), ("allowed", Permission.ALLOW)],
    )
    task = asyncio.create_task(graph.ainvoke(state("ask", "allowed"), context=context))
    try:
        await asyncio.wait_for(broker.requested.wait(), 2)
        await asyncio.wait_for(executed.wait(), 2)
        broker.answer.set_result(
            {"decision": "reject", "stop": True, "message": "用户停止了本轮"}
        )
        output = await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert seen == ["allowed"]
    assert output["tool_cancelled"] is True
    assert [m.tool_call_id for m in results(output)] == ["ask", "allowed"]


async def test_real_broker_streams_separate_approval_events(tmp_path):
    from lumi.agents.core.broker import LUMI_APPROVAL_EVENT, ApprovalBroker

    broker = ApprovalBroker()
    graph, context, seen, executed = setup_run(
        tmp_path,
        "default",
        broker,
        [
            ("first", Permission.ASK),
            ("second", Permission.ASK),
            ("allowed", Permission.ALLOW),
        ],
    )
    requests = []
    ready = asyncio.Event()

    async def consume():
        async for event in graph.astream_events(
            state("first", "second", "allowed"), context=context, version="v2"
        ):
            if (
                event["event"] == "on_custom_event"
                and event["name"] == LUMI_APPROVAL_EVENT
            ):
                requests.append(event["data"])
                if len(requests) == 2:
                    ready.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), 2)
        await asyncio.wait_for(executed.wait(), 2)
        assert seen == ["allowed"]
        assert [[tc["id"] for tc in r["tool_calls"]] for r in requests] == [
            ["first"],
            ["second"],
        ]
        assert broker.resolve(requests[0]["approval_id"], {"decision": "reject"})
        assert broker.resolve(requests[1]["approval_id"], {"decision": "approve"})
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert set(seen) == {"allowed", "second"}
    assert not broker._pending

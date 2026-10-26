"""Real Lumi graph and SQLite checkpoint across turns, without model network calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import resolved
from langchain_core.messages import AIMessage, HumanMessage

from lumi.agents.core import nodes
from lumi.agents.core.graph import LumiAgent, create_checkpointer
from lumi.agents.core.state import LumiAgentContext
from lumi.agents.runtime.subagents.service import SubagentService


async def test_lumi_preserves_independent_checkpoints_and_depth(
    tmp_path, isolated_config, monkeypatch
):
    calls, inputs = [], []

    async def create(**kwargs):
        calls.append(kwargs)
        saver = await create_checkpointer(kwargs["checkpoint"])
        return LumiAgent(saver), LumiAgentContext(model_name="fake-model", tools=[])

    async def model(messages):
        messages = messages["messages"]
        inputs.append(messages)
        return AIMessage(
            content=[
                {"type": "thinking", "thinking": "PRIVATE", "signature": "sig"},
                {"type": "text", "text": "answer"},
                {"type": "text", "text": "part2"},
            ]
        )

    monkeypatch.setattr("lumi.agents.core.graph.create_agent", create)
    monkeypatch.setattr(
        "lumi.agents.tools.get_tools",
        AsyncMock(
            return_value=[
                SimpleNamespace(name="agent_create"),
                SimpleNamespace(name="ask"),
                SimpleNamespace(name="read"),
            ]
        ),
    )
    monkeypatch.setattr(
        nodes, "tool_call_chain", lambda *_, **__: SimpleNamespace(ainvoke=model)
    )
    monkeypatch.setattr(nodes, "detect_protocol", lambda *_: "openai")
    monkeypatch.setattr(nodes, "resolve", lambda *_: resolved(0))
    service = SubagentService(tmp_path / "subagents.db")
    try:
        first = service.create(
            "parent",
            str(tmp_path),
            {"name": "worker", "system_prompt": "role"},
            3,
            "one",
            notify=False,
            title="测试任务",
        )
        assert (await service.wait(first["turn_id"]))["output"] == "answer\npart2"
        second = service.message("parent", first["agent_id"], "two", notify=False)
        assert (await service.wait(second["turn_id"]))["output"] == "answer\npart2"
        other = service.create(
            "parent",
            str(tmp_path),
            {"name": "other"},
            1,
            "different",
            notify=False,
            title="测试任务",
        )
        await service.wait(other["turn_id"])
        assert len(inputs[0]) == 1 and len(inputs[1]) == 3 and len(inputs[2]) == 1
        assert isinstance(inputs[1][0], HumanMessage) and "one" in str(
            inputs[1][0].content
        )
        assert inputs[1][-1].id == second["turn_id"] + ":input"
        assert calls[0]["enable_memory"] is False
        assert {t.name for t in calls[0]["tools"]} == {"read", "ask"}
        assert calls[0]["system_prompt"] == "role"
        # Reopened SQLite graph sees the persisted depth and messages under the subagent ID.
        saver = await create_checkpointer(calls[0]["checkpoint"])
        graph = LumiAgent(saver)
        try:
            from lumi.sessions.session_store import list_sessions

            assert await list_sessions(graph.graph) == []
            snapshot = await graph.graph.aget_state(
                {"configurable": {"thread_id": first["agent_id"]}}
            )
            assert snapshot.values["depth"] == 3
            assert len(snapshot.values["messages"]) == 4
            parent = await graph.graph.aget_state(
                {"configurable": {"thread_id": "parent"}}
            )
            assert not parent.values
        finally:
            await graph.aclose()
    finally:
        await service.close()


@pytest.mark.parametrize("notify", [False, True])
async def test_lumi_ask_is_checkpointed_and_resumes_after_runtime_restart(
    tmp_path, isolated_config, monkeypatch, notify
):
    model_inputs = []

    async def create(**kwargs):
        saver = await create_checkpointer(kwargs["checkpoint"])
        return LumiAgent(saver), LumiAgentContext(
            model_name="fake", tools=kwargs["tools"]
        )

    async def model(inputs):
        model_inputs.append(inputs["messages"])
        if len(model_inputs) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "format-question",
                        "name": "ask",
                        "args": {
                            "questions": [
                                {
                                    "question": "需要什么格式？",
                                    "header": "格式",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "CSV", "description": "表格"},
                                        {"label": "JSON", "description": "结构化"},
                                    ],
                                }
                            ]
                        },
                    }
                ],
            )
        return AIMessage(content="已按 JSON 继续原任务")

    monkeypatch.setattr("lumi.agents.core.graph.create_agent", create)
    monkeypatch.setattr("lumi.agents.tools.get_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        nodes, "tool_call_chain", lambda *_, **__: SimpleNamespace(ainvoke=model)
    )
    monkeypatch.setattr(nodes, "detect_protocol", lambda *_: "openai")
    monkeypatch.setattr(nodes, "resolve", lambda *_: resolved(0))
    path = tmp_path / "questions.db"
    service = SubagentService(path)
    try:
        first = service.create(
            "parent",
            str(tmp_path),
            {"name": "worker"},
            1,
            "开始",
            notify=notify,
            tool_mode="privileged",
            title="测试任务",
        )
        result = await service.wait(first["turn_id"])
        assert result["status"] == "blocked"
        assert result["questions"][0]["question"] == "需要什么格式？"
        assert result["questions"][0]["options"][1]["label"] == "JSON"
        assert len(model_inputs) == 1  # ask ended the graph without another model step.
        events = service.history("parent", first["agent_id"])["events"]
        assert not any(event["type"] == "question.request" for event in events)
        question_events = [
            event for event in events if event["payload"].get("questions")
        ]
        assert len(question_events) == 1
        assert question_events[0]["type"] == "turn.complete"
        assert question_events[0]["payload"]["questions"] == result["questions"]
        await service.close()
        service = SubagentService(path)
        assert service.receipt(first["turn_id"]) == result
        deliveries = service.store.pending_deliveries("parent")
        assert len(deliveries) == int(notify)
        if notify:
            assert deliveries[0]["questions"] == result["questions"]
        reply = service.message(
            "parent", first["agent_id"], "格式问题的答案：JSON；请继续", notify=False
        )
        completed = await service.wait(reply["turn_id"])
        assert completed["status"] == "completed"
        assert completed["output"] == "已按 JSON 继续原任务"
        assert completed["agent_id"] == first["agent_id"]
        assert any(
            getattr(message, "tool_call_id", "") == "format-question"
            for message in model_inputs[1]
        )
        assert "questions" not in completed
    finally:
        await service.close()

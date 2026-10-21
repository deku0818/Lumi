"""Exercise the actual stdio adapter with a local, deterministic app-server peer."""

import asyncio
import sys
from dataclasses import replace

import pytest

from lumi.agents.runtime.subagents.codex import CodexExecutor
from lumi.agents.runtime.subagents.types import (
    Subagent,
    SubagentBlockedError,
    SubagentDeliveryUnknownError,
    SubagentQuestionError,
    SubagentTurn,
)

PEER = r"""
import json, sys
from pathlib import Path
history = Path("history.json")
def send(value):
    print(json.dumps(value), flush=True)
def notify(method, **params):
    send({"method": method, "params": {"threadId": "native-42", **params}})
def ask(tid):
    send({"id": "question-1", "method": "item/tool/requestUserInput",
          "params": {"threadId": "native-42", "turnId": tid,
                     "questions": [{"id": "format", "header": "Format",
                                    "question": "Which output format?",
                                    "options": [{"label": "CSV", "description": "Table"},
                                                {"label": "JSON", "description": "Structured"}]}]}})
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params", {})
    if method == "initialize":
        result = {"userAgent": "fixture"}
    elif method in ("thread/start", "thread/resume"):
        assert params["approvalPolicy"] == "never"
        assert params["sandbox"] == "danger-full-access"
        result = {"thread": {"id": params.get("threadId", "native-42")}}
    elif method == "turn/start":
        prompt = params["input"][0]["text"]
        if prompt == "disconnect":
            sys.exit(0)
        old = json.loads(history.read_text()) if history.exists() else []
        old.append(prompt)
        history.write_text(json.dumps(old))
        tid = "turn-" + str(len(old))
        notify("turn/completed", turn={"id": "previous", "status": "failed"})
        # Notifications are allowed to arrive before the turn/start response.
        notify("item/agentMessage/delta", turnId=tid, itemId="m", delta="stream")
        if prompt == "early-question":
            ask(tid)
        send({"id": request["id"], "result": {"turn": {"id": tid}}})
        if prompt == "approval":
            send({"id": "approval-1", "method": "item/commandExecution/requestApproval",
                  "params": {"threadId": "native-42", "turnId": tid}})
            reply = json.loads(sys.stdin.readline())
            assert reply["result"]["decision"] == "decline"
        if prompt in ("question", "early-question", "question-no-terminal", "question-interrupt-error", "question-interrupt-timeout"):
            if prompt != "early-question":
                ask(tid)
            control = json.loads(sys.stdin.readline())
            if control.get("method") == "turn/interrupt":
                assert control["params"] == {"threadId": "native-42", "turnId": tid}
                if prompt == "question-interrupt-timeout":
                    continue
                if prompt == "question-interrupt-error":
                    send({"id": control["id"], "error": {"code": -32600, "message": "interrupt refused"}})
                else:
                    send({"id": control["id"], "result": {}})
                    if prompt != "question-no-terminal":
                        notify("turn/completed", turn={"id": tid, "status": "interrupted"})
                continue
            # An empty answer resumes the native turn before the parent can decide.
            Path("post-question-action").write_text("executed")
            notify("item/started", turnId=tid, item={"id":"post-question", "type":"commandExecution", "command":"unsafe continuation"})
        notify("item/completed", turnId="different", item={"id":"bad", "type":"agentMessage", "text":"WRONG"})
        notify("item/completed", turnId=tid, item={"id":"m", "type":"agentMessage", "text":" / ".join(old)})
        notify("turn/completed", turn={"id":tid, "status":"failed" if prompt == "fail" else "completed"})
        continue
    else:
        continue
    send({"id": request["id"], "result": result})
"""


@pytest.fixture
def agent(tmp_path):
    peer = tmp_path / "peer.py"
    peer.write_text(PEER)
    return Subagent(
        "subagent_1",
        "parent",
        str(tmp_path),
        "codex",
        "codex",
        {"name": "codex", "command": [sys.executable, str(peer)], "system_prompt": ""},
        1,
        "",
        "default",
        {},
        0,
    )


def turn(prompt, n=1):
    return SubagentTurn(
        f"bg_{n}", "subagent_1", n, prompt, "running", True, "", "", 0, None
    )


async def test_two_turns_and_resume_after_process_restart(agent):
    executor = CodexExecutor()
    events, handle = [], {}
    try:
        assert (
            await asyncio.wait_for(
                executor.run(
                    agent,
                    turn("one"),
                    lambda k, p: events.append((k, p)),
                    handle.update,
                ),
                3,
            )
            == "one"
        )
        process = executor.process
        assert (
            await executor.run(
                replace(agent, handle=handle),
                turn("two", 2),
                lambda *_: None,
                handle.update,
            )
            == "one / two"
        )
        assert executor.process is process
        assert any(k == "message.delta" for k, _ in events)
        assert handle == {"thread_id": "native-42"}
        await executor.close()
        assert process.returncode is not None
        assert (
            await executor.run(
                replace(agent, handle=handle),
                turn("three", 3),
                lambda *_: None,
                handle.update,
            )
            == "one / two / three"
        )
    finally:
        await executor.close()


@pytest.mark.parametrize(
    "prompt,error",
    [
        ("approval", SubagentBlockedError),
        ("disconnect", SubagentDeliveryUnknownError),
        ("fail", RuntimeError),
    ],
)
async def test_native_failure_is_never_reported_as_success(agent, prompt, error):
    executor = CodexExecutor()
    try:
        with pytest.raises(error):
            await asyncio.wait_for(
                executor.run(agent, turn(prompt), lambda *_: None, lambda _: None), 3
            )
        if prompt == "disconnect":
            assert not (
                __import__("pathlib").Path(agent.workspace) / "history.json"
            ).exists()
    finally:
        await executor.close()


async def test_cancel_kills_owned_app_server(agent):
    executor = CodexExecutor()
    await executor.run(agent, turn("one"), lambda *_: None, lambda _: None)
    process = executor.process
    await executor.close()
    assert process.returncode is not None
    assert executor._reader is None


@pytest.mark.parametrize(
    "prompt",
    [
        "question",
        "early-question",
        "question-no-terminal",
        "question-interrupt-error",
        "question-interrupt-timeout",
    ],
)
async def test_question_preserves_details_and_continues_same_native_thread(
    agent, prompt, monkeypatch
):
    from pathlib import Path

    executor, handle = CodexExecutor(), {}
    if prompt == "question-interrupt-timeout":
        monkeypatch.setattr(
            "lumi.agents.runtime.subagents.codex.GRACEFUL_SHUTDOWN_TIMEOUT", 0.05
        )
    events = []
    try:
        with pytest.raises(SubagentQuestionError) as error:
            await asyncio.wait_for(
                executor.run(
                    agent,
                    turn(prompt),
                    lambda k, p: events.append((k, p)),
                    handle.update,
                ),
                3,
            )
        assert error.value.questions[0]["id"] == "format"
        assert error.value.questions[0]["options"][1]["label"] == "JSON"
        assert handle == {"thread_id": "native-42"}
        assert not (Path(agent.workspace) / "post-question-action").exists()
        assert not any(
            payload.get("tool_call_id") == "post-question" for _, payload in events
        )
        assert executor.process is None
        assert (
            await executor.run(
                replace(agent, handle=handle),
                turn("format: JSON", 2),
                lambda *_: None,
                handle.update,
            )
            == f"{prompt} / format: JSON"
        )
    finally:
        await executor.close()


@pytest.mark.parametrize("answer_source", ["parent", "user", "cancel"])
async def test_parent_handles_question_using_existing_ask_and_agent_message(
    agent, tmp_path, monkeypatch, answer_source
):
    from types import SimpleNamespace

    from conftest import resolved
    from langchain_core.messages import AIMessage, HumanMessage

    from lumi.agents.core import nodes
    from lumi.agents.core.graph import LumiAgent
    from lumi.agents.core.state import LumiAgentContext
    from lumi.agents.runtime.subagents.service import (
        SubagentService,
        result_notification,
    )
    from lumi.agents.tools.providers.agent import agent_message
    from lumi.agents.tools.providers.ask import ASK_CANCELLED, ask

    service = SubagentService(tmp_path / "questions.db")
    parent = LumiAgent()
    asked = []
    stages = []

    class Broker:
        async def request(self, payload, reject_value):
            asked.append(payload)
            return ASK_CANCELLED if answer_source == "cancel" else "JSON"

    async def model(inputs):
        stages.append(inputs["messages"])
        if answer_source != "parent" and len(stages) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "ask-user",
                        "name": "ask",
                        "args": {
                            "questions": [
                                {
                                    "question": "Which output format?",
                                    "header": "Format",
                                    "options": [
                                        {"label": "CSV", "description": "Table"},
                                        {"label": "JSON", "description": "Structured"},
                                    ],
                                    "multiSelect": False,
                                }
                            ]
                        },
                    }
                ],
            )
        if not any(
            getattr(message, "name", "") == "agent_message"
            for message in inputs["messages"]
        ):
            if answer_source == "parent":
                assert any(
                    "已确认输出格式为 JSON" in str(message.content)
                    for message in inputs["messages"]
                )
            else:
                assert any(
                    getattr(message, "tool_call_id", "") == "ask-user"
                    and message.content == "JSON"
                    for message in inputs["messages"]
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "reply-to-original",
                        "name": "agent_message",
                        "args": {
                            "agent_id": first["agent_id"],
                            "prompt": "format: JSON",
                            "run_in_background": False,
                        },
                    }
                ],
            )
        return AIMessage(content="Done")

    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    monkeypatch.setattr(
        nodes, "tool_call_chain", lambda *_, **__: SimpleNamespace(ainvoke=model)
    )
    monkeypatch.setattr(nodes, "detect_protocol", lambda *_: "openai")
    monkeypatch.setattr(nodes, "resolve", lambda *_: resolved(0))
    try:
        first = service.create(
            "parent",
            agent.workspace,
            {**agent.definition, "executor": "codex"},
            1,
            "question",
        )
        receipt = await service.wait(first["turn_id"])
        assert receipt["status"] == "blocked"
        assert receipt["questions"][0]["id"] == "format"
        assert "ask" in receipt["next_action"]
        delivery = service.store.pending_deliveries("parent")[0]
        notification = result_notification(delivery)
        assert "Which output format?" in notification
        assert first["agent_id"] in notification
        await parent.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="已确认输出格式为 JSON。"
                        if answer_source == "parent"
                        else "请完成子任务。"
                    ),
                    HumanMessage(content=notification),
                ]
            },
            config={"configurable": {"thread_id": "parent"}},
            context=LumiAgentContext(
                tools=[ask, agent_message],
                model_name="fake",
                tool_mode="privileged",
                project_dir=tmp_path,
                approval_broker=Broker(),
            ),
        )
        assert len(asked) == (0 if answer_source == "parent" else 1)
        agents = service.list_agents("parent")
        assert len(agents) == 1 and agents[0]["agent_id"] == first["agent_id"]
        if answer_source == "cancel":
            assert agents[0]["turn_count"] == 1
        else:
            assert agents[0]["turn_count"] == 2
            continued = service.receipt(agents[0]["last_turn_id"])
            assert continued["status"] == "completed"
            assert continued["output"] == "question / format: JSON"
            assert "questions" not in continued
        assert service.store.agent(first["agent_id"]).handle == {
            "thread_id": "native-42"
        }
    finally:
        await parent.aclose()
        await service.close()

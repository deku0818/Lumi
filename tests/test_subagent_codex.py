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
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get("method"), request.get("params", {})
    if method == "initialize":
        result = {"userAgent": "fixture"}
    elif method in ("thread/start", "thread/resume"):
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
        send({"id": request["id"], "result": {"turn": {"id": tid}}})
        if prompt == "approval":
            send({"id": "approval-1", "method": "item/commandExecution/requestApproval",
                  "params": {"threadId": "native-42", "turnId": tid}})
            reply = json.loads(sys.stdin.readline())
            assert reply["result"]["decision"] == "decline"
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

"""Main-chat and persisted-subagent adapters must agree on tool identity."""

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from lumi.agents.runtime.subagents.lumi import translate_lumi_event
from lumi.gateway.bridge import AgentBridge


@pytest.mark.parametrize("phase", ["start", "end", "error"])
@pytest.mark.parametrize(
    "metadata,input_id,expected_id",
    [
        ({"lumi_tool_call_id": "call-original"}, "call-input", "call-original"),
        ({}, "call-input", "call-input"),
        ({}, "", "run-id"),
    ],
)
def test_tool_identity_and_argument_visibility_across_adapters(
    phase, metadata, input_id, expected_id
):
    args = {"path": "notes.md", "tool_call_id": input_id, "runtime": object()}
    event = {
        "event": f"on_tool_{phase}",
        "name": "read",
        "run_id": "run-id",
        "metadata": metadata,
        "data": {"input": args},
    }
    if phase == "error":
        event["data"]["error"] = RuntimeError("cannot read")
    else:
        event["data"]["output"] = ToolMessage(
            content="contents", tool_call_id="call-original"
        )
    (main,) = AgentBridge()._translate_event(event)
    subagent_events = []
    translate_lumi_event(
        event, lambda kind, payload: subagent_events.append((kind, payload))
    )
    kind, subagent = subagent_events[0]
    assert main.tool_call_id == subagent["tool_call_id"] == expected_id
    assert kind == main.kind == ("tool.start" if phase == "start" else "tool.complete")
    if phase == "start":
        assert main.args == subagent["args"] == {"path": "notes.md"}
        assert "runtime" in args and "tool_call_id" in args
    elif phase == "end":
        assert main.output == subagent["output"] == "contents"
        assert not main.is_error and not subagent["is_error"]
    else:
        assert main.is_error and subagent["is_error"]
        assert "cannot read" in main.output and "cannot read" in subagent["output"]


@pytest.mark.parametrize("inp", [None, "raw input"])
def test_non_dictionary_tool_input_uses_run_identity(inp):
    event = {"event": "on_tool_start", "run_id": "run-id", "data": {"input": inp}}
    (main,) = AgentBridge()._translate_event(event)
    subagent_events = []
    translate_lumi_event(event, lambda _, payload: subagent_events.append(payload))
    assert main.args == subagent_events[0]["args"] == {}
    assert main.tool_call_id == subagent_events[0]["tool_call_id"] == "run-id"


def test_main_tool_completion_preserves_command_results_and_control_flow():
    bridge = AgentBridge()
    event = {
        "event": "on_tool_end",
        "data": {
            "output": Command(
                update={
                    "messages": [
                        ToolMessage(content="denied", status="error", tool_call_id="c1")
                    ]
                }
            )
        },
    }
    (result,) = bridge._translate_event(event)
    assert result.output == "denied" and result.is_error
    assert not list(
        bridge._translate_event(
            {"event": "on_tool_error", "data": {"error": GraphBubbleUp()}}
        )
    )

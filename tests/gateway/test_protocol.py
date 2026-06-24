"""protocol.bridge_event_to_wire 映射测试（纯函数断言）。"""

from __future__ import annotations

from lumi.gateway.bridge import BridgeEvent, EventKind
from lumi.gateway.protocol import bridge_event_to_wire

SID = "thread-123"


def test_stream_token_maps_to_message_delta():
    wire = bridge_event_to_wire(
        BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="你好"), SID
    )["params"]
    assert wire == {
        "type": "message.delta",
        "session_id": SID,
        "payload": {"text": "你好"},
    }


def test_stream_token_carries_usage_when_present():
    wire = bridge_event_to_wire(
        BridgeEvent(
            kind=EventKind.MESSAGE_DELTA, text="x", usage_metadata={"input_tokens": 3}
        ),
        SID,
    )["params"]
    assert wire["payload"]["usage"] == {"input_tokens": 3}


def test_tool_start_maps_with_args_and_id():
    wire = bridge_event_to_wire(
        BridgeEvent(
            kind=EventKind.TOOL_START,
            name="bash",
            args={"command": "ls"},
            tool_call_id="call_1",
        ),
        SID,
    )["params"]
    assert wire["type"] == "tool.start"
    assert wire["payload"] == {
        "name": "bash",
        "args": {"command": "ls"},
        "tool_call_id": "call_1",
    }


def test_tool_start_includes_run_id_for_subagent():
    wire = bridge_event_to_wire(
        BridgeEvent(kind=EventKind.TOOL_START, name="agent", run_id="run_9"), SID
    )["params"]
    assert wire["payload"]["run_id"] == "run_9"


def test_tool_end_maps_to_tool_complete():
    wire = bridge_event_to_wire(
        BridgeEvent(
            kind=EventKind.TOOL_COMPLETE,
            name="bash",
            output="done",
            tool_call_id="call_1",
        ),
        SID,
    )["params"]
    assert wire["type"] == "tool.complete"
    assert wire["payload"]["output"] == "done"


def test_tool_approval_passes_data_through():
    data = {"tool_calls": [{"name": "bash"}], "options": [{"key": "reject"}]}
    wire = bridge_event_to_wire(BridgeEvent(kind=EventKind.APPROVAL, data=data), SID)[
        "params"
    ]
    assert wire["type"] == "approval.request"
    assert wire["payload"] == data


def test_ask_maps_to_clarify_request():
    wire = bridge_event_to_wire(
        BridgeEvent(kind=EventKind.CLARIFY, data={"question": "确认?"}), SID
    )["params"]
    assert wire["type"] == "clarify.request"
    assert wire["payload"] == {"question": "确认?"}


def test_done_maps_to_turn_complete():
    wire = bridge_event_to_wire(BridgeEvent(kind=EventKind.TURN_COMPLETE), SID)[
        "params"
    ]
    assert wire == {"type": "turn.complete", "session_id": SID, "payload": {}}


def test_error_maps_message():
    wire = bridge_event_to_wire(BridgeEvent(kind=EventKind.ERROR, error="boom"), SID)[
        "params"
    ]
    assert wire == {
        "type": "error",
        "session_id": SID,
        "payload": {"message": "boom"},
    }


def test_parent_run_id_injected_into_payload():
    wire = bridge_event_to_wire(
        BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="x", parent_run_id="p1"), SID
    )["params"]
    assert wire["payload"]["parent_run_id"] == "p1"

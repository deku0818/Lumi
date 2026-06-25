"""PR2 单测：ACP session/update 归一化 + bridge 映射成 BridgeEvent。

纯函数断言，不起子进程、不连网络（委派工具的端到端链路在 test_acp_client.py 验过传输层）。
"""

import acp

from lumi.agents.tools.providers.external_agent import _normalize_acp_update
from lumi.gateway.bridge.core import AgentBridge, EventKind

# ── _normalize_acp_update：ACP update → 归一化 payload ──


def test_normalize_message_chunk():
    update = acp.update_agent_message_text("hello")
    assert _normalize_acp_update(update) == {"kind": "message", "text": "hello"}


def test_normalize_thought_chunk():
    update = acp.update_agent_thought_text("thinking")
    assert _normalize_acp_update(update) == {"kind": "thought", "text": "thinking"}


def test_normalize_empty_message_skipped():
    # 空文本无渲染价值，归一化为 None（跳过）
    assert _normalize_acp_update(acp.update_agent_message_text("")) is None


def test_normalize_tool_call_start():
    update = acp.start_tool_call("tc-1", "Read foo.py", kind="read")
    assert _normalize_acp_update(update) == {
        "kind": "tool_start",
        "name": "Read foo.py",
        "tool_call_id": "tc-1",
    }


def test_normalize_tool_call_completed():
    update = acp.update_tool_call("tc-1", title="Read foo.py", status="completed")
    assert _normalize_acp_update(update) == {
        "kind": "tool_complete",
        "name": "Read foo.py",
        "tool_call_id": "tc-1",
        "is_error": False,
    }


def test_normalize_tool_call_failed_marks_error():
    update = acp.update_tool_call("tc-2", status="failed")
    out = _normalize_acp_update(update)
    assert out["kind"] == "tool_complete" and out["is_error"] is True


def test_normalize_in_progress_tool_call_skipped():
    # 中间态（in_progress）不收尾卡片，跳过
    assert _normalize_acp_update(acp.update_tool_call("tc-1", status="in_progress")) is None


# ── _acp_event_to_bridge：归一化 payload → BridgeEvent ──


def test_bridge_maps_message_to_delta():
    evt = AgentBridge._acp_event_to_bridge({"kind": "message", "text": "hi"}, "parent-1")
    assert evt.kind == EventKind.MESSAGE_DELTA
    assert evt.text == "hi"
    assert evt.parent_run_id == "parent-1"


def test_bridge_maps_thought_to_thinking():
    evt = AgentBridge._acp_event_to_bridge({"kind": "thought", "text": "t"}, "p")
    assert evt.kind == EventKind.THINKING_DELTA


def test_bridge_maps_tool_start():
    evt = AgentBridge._acp_event_to_bridge(
        {"kind": "tool_start", "name": "Read", "tool_call_id": "tc-1"}, "p"
    )
    assert evt.kind == EventKind.TOOL_START
    assert (evt.name, evt.tool_call_id, evt.parent_run_id) == ("Read", "tc-1", "p")


def test_bridge_maps_tool_complete_with_error():
    evt = AgentBridge._acp_event_to_bridge(
        {"kind": "tool_complete", "name": "Edit", "tool_call_id": "tc-1", "is_error": True},
        "p",
    )
    assert evt.kind == EventKind.TOOL_COMPLETE
    assert evt.is_error is True


def test_bridge_unknown_kind_returns_none():
    assert AgentBridge._acp_event_to_bridge({"kind": "plan"}, "p") is None

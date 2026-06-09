"""Desktop WS 服务对外事件协议。

把内部 BridgeEvent 序列化为线缆事件信封 {type, session_id, payload}，对齐
hermes-agent 的 GatewayEvent 设计：扁平信封 + payload 按事件类型分类。

事件名（type）直接来自 EventKind 的成员值（namespace.verb，见 protocol/events.json
单一事实来源）——无需额外映射层。本模块只负责把 BridgeEvent 的扁平字段重组成
每个事件类型有意义的 payload 子集。
"""

from __future__ import annotations

from lumi.agents.bridge import BridgeEvent, EventKind


def _payload(evt: BridgeEvent) -> dict:
    """按事件类型构造 payload，只保留该类型有意义的字段。"""
    kind = evt.kind
    if kind == EventKind.MESSAGE_DELTA:
        payload = {"text": evt.text}
        if evt.usage_metadata:
            payload["usage"] = evt.usage_metadata
        return payload
    if kind in (EventKind.MESSAGE_COMPLETE, EventKind.TURN_COMPLETE):
        return {"usage": evt.usage_metadata} if evt.usage_metadata else {}
    if kind == EventKind.TOOL_START:
        payload = {
            "name": evt.name,
            "args": evt.args or {},
            "tool_call_id": evt.tool_call_id,
        }
        if evt.run_id:
            payload["run_id"] = evt.run_id
        return payload
    if kind == EventKind.TOOL_COMPLETE:
        return {
            "name": evt.name,
            "output": evt.output,
            "tool_call_id": evt.tool_call_id,
        }
    if kind in (EventKind.CLARIFY, EventKind.APPROVAL, EventKind.PLAN):
        return evt.data or {}
    if kind == EventKind.ERROR:
        return {"message": evt.error}
    return {}


def bridge_event_to_wire(evt: BridgeEvent, session_id: str) -> dict:
    """把 BridgeEvent 序列化为线缆事件信封 {type, session_id, payload}。

    type 直接取 EventKind 成员值（已是 namespace.verb wire 名）。
    """
    payload = _payload(evt)
    if evt.parent_run_id:
        payload["parent_run_id"] = evt.parent_run_id
    return {
        "type": str(evt.kind),
        "session_id": session_id,
        "payload": payload,
    }

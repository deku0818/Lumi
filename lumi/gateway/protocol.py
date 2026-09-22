"""gateway 对外事件协议。

把内部 BridgeEvent 序列化为线缆事件信封 {type, session_id, payload}：扁平信封 +
payload 按事件类型分类。

事件名（type）直接来自 EventKind 的成员值（namespace.verb，见 protocol/events.json
单一事实来源）——无需额外映射层。本模块只负责把 BridgeEvent 的扁平字段重组成
每个事件类型有意义的 payload 子集。
"""

from __future__ import annotations

from enum import StrEnum

from lumi.gateway.bridge import BridgeEvent, EventKind


class ServerEvent(StrEnum):
    """不经 BridgeEvent、由服务端直接广播的 wire 事件。

    与 EventKind 同样「成员值直接 = wire 名」，两者合起来就是后端产出的全部事件——
    契约测试据此与 protocol/events.json 比对，故新增广播事件必须登记在这里，
    而不是在发送点写字符串字面量（那样漂移测不出来）。
    """

    GATEWAY_READY = "gateway.ready"
    CRON_RESULT = "cron.result"
    CRON_RUNNING = "cron.running"
    CRON_JOBS = "cron.jobs"
    BG_TASKS_UPDATE = "bg_tasks.update"
    CHANNEL_ACTIVITY = "channel.activity"
    SESSION_TITLE = "session.title"
    MCP_STATUS = "mcp.status"
    ENV_PROGRESS = "env.progress"
    ENV_STATE = "env.state"


def _payload(evt: BridgeEvent) -> dict:
    """按事件类型构造 payload，只保留该类型有意义的字段。"""
    kind = evt.kind
    if kind in (EventKind.MESSAGE_DELTA, EventKind.THINKING_DELTA):
        payload = {"text": evt.text}
        if evt.usage_metadata:
            payload["usage"] = evt.usage_metadata
        return payload
    if kind in (EventKind.MESSAGE_COMPLETE, EventKind.TURN_COMPLETE):
        return {"usage": evt.usage_metadata} if evt.usage_metadata else {}
    if kind == EventKind.TURN_START:
        return {"message_id": evt.message_id}
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
        payload = {
            "name": evt.name,
            "output": evt.output,
            "tool_call_id": evt.tool_call_id,
        }
        if evt.is_error:
            payload["is_error"] = True
        return payload
    if kind == EventKind.COMPACTION_STATUS:
        return {"active": bool(evt.data and evt.data.get("active"))}
    if kind == EventKind.TODOS_UPDATE:
        return {"todos": (evt.data or {}).get("todos", [])}
    if kind in (EventKind.CLARIFY, EventKind.APPROVAL):
        return evt.data or {}
    if kind == EventKind.ERROR:
        return {"message": evt.error}
    return {}


def event_frame(event_type: str, session_id: str, payload: dict) -> dict:
    """构造完整 wire 事件帧 {method:"event", params:{type, session_id, payload}}。

    服务端任何直发事件（gateway.ready、cron.*、合成 turn.complete 等不经
    BridgeEvent 的）都必须经此构造，信封形状只在这一处定义。
    """
    return {
        "method": "event",
        "params": {"type": event_type, "session_id": session_id, "payload": payload},
    }


def bridge_event_to_wire(evt: BridgeEvent, session_id: str) -> dict:
    """把 BridgeEvent 序列化为完整 wire 事件帧（可直接 send_json）。

    type 直接取 EventKind 成员值（已是 namespace.verb wire 名）。
    """
    payload = _payload(evt)
    if evt.parent_run_id:
        payload["parent_run_id"] = evt.parent_run_id
    return event_frame(str(evt.kind), session_id, payload)

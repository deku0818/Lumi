"""Desktop WS 服务对外事件协议。

将内部 BridgeEvent 映射为 namespace.verb 风格的线缆事件，对齐 hermes-agent
的 GatewayEvent 设计：扁平信封 {type, session_id, payload}，payload 按事件类型
强类型。前端（Electron / web）据此渲染，无需感知 LangGraph / BridgeEvent。

事件名（对外契约，前端 TS 类型应与此一致）：
    gateway.ready        连接就绪（握手），payload: {model}
    message.start        一次 LLM 输出开始
    message.delta        流式文本增量，payload: {text, usage?}
    message.complete     一次 LLM 输出结束，payload: {usage?}
    tool.generating      模型正在生成工具调用参数
    tool.start           工具开始执行，payload: {name, args, tool_call_id, run_id?}
    tool.complete        工具执行结束，payload: {name, output, tool_call_id}
    clarify.request      向用户提问（ask），需 resume
    approval.request     工具审批（含 options/warnings/boundary_violations），需 resume
    plan.request         退出 plan mode 确认，需 resume
    turn.complete        整轮对话结束，payload: {usage?}
    error                执行错误，payload: {message}

子代理事件在 payload 附带 parent_run_id（非空时）。
"""

from __future__ import annotations

from lumi.agents.bridge import BridgeEvent, EventKind

# 内部 EventKind → 对外事件名（namespace.verb）
_EVENT_TYPE: dict[EventKind, str] = {
    EventKind.MODEL_START: "message.start",
    EventKind.STREAM_TOKEN: "message.delta",
    EventKind.MODEL_END: "message.complete",
    EventKind.TOOL_CALL_CHUNK: "tool.generating",
    EventKind.TOOL_START: "tool.start",
    EventKind.TOOL_END: "tool.complete",
    EventKind.ASK: "clarify.request",
    EventKind.TOOL_APPROVAL: "approval.request",
    EventKind.EXIT_PLAN_MODE: "plan.request",
    EventKind.DONE: "turn.complete",
    EventKind.ERROR: "error",
}


def _payload(evt: BridgeEvent) -> dict:
    """按事件类型构造 payload，只保留该类型有意义的字段。"""
    kind = evt.kind
    if kind == EventKind.STREAM_TOKEN:
        payload = {"text": evt.text}
        if evt.usage_metadata:
            payload["usage"] = evt.usage_metadata
        return payload
    if kind in (EventKind.MODEL_END, EventKind.DONE):
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
    if kind == EventKind.TOOL_END:
        return {
            "name": evt.name,
            "output": evt.output,
            "tool_call_id": evt.tool_call_id,
        }
    if kind in (EventKind.ASK, EventKind.TOOL_APPROVAL, EventKind.EXIT_PLAN_MODE):
        return evt.data or {}
    if kind == EventKind.ERROR:
        return {"message": evt.error}
    return {}


def bridge_event_to_wire(evt: BridgeEvent, session_id: str) -> dict:
    """把 BridgeEvent 映射为线缆事件信封 {type, session_id, payload}。"""
    payload = _payload(evt)
    if evt.parent_run_id:
        payload["parent_run_id"] = evt.parent_run_id
    return {
        "type": _EVENT_TYPE[evt.kind],
        "session_id": session_id,
        "payload": payload,
    }

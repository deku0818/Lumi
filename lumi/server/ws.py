"""Desktop WebSocket 服务：把 AgentBridge 暴露为 JSON-RPC over WS。

帧协议（client ↔ server）：
    client → server  {id, method, params}
        send_message    params: {content, tool_mode?, execution_mode?}   → 流式
        resume          params: {value}                                   → 流式
        list_sessions   params: {limit?}                                  → {sessions:[...]}
        new_session     params: {}                                        → {thread_id}
        switch_session  params: {thread_id}                               → {thread_id}
        load_history    params: {thread_id}                               → {items:[...]}
        pin_session     params: {thread_id, pinned}                       → {thread_id, pinned}
        rename_session  params: {thread_id, title}                        → {thread_id, title}
        delete_session  params: {thread_id}                               → {thread_id}
    server → client
        事件帧  {method: "event", params: <wire event>}   # 见 protocol.py
        响应帧  {id, result}  或  {id, error: {message}}

一个 WS 连接 = 一个会话上下文（独立 AgentBridge，可切换 thread）。一次只跑一轮：
run 进行时不读新帧，中断（approval/clarify/plan）后回到接收循环等待 resume。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lumi.agents.bridge import AgentBridge
from lumi.server.protocol import bridge_event_to_wire
from lumi.tui.session_meta import delete_meta, load_all, update_meta
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id
from lumi.utils.workspace_id import get_workspace_dir


@asynccontextmanager
async def lifespan(app: FastAPI):
    from lumi.utils.patches import apply_all
    from lumi.utils.read_config import get_config

    apply_all()
    get_config().apply_env()
    yield


app = FastAPI(lifespan=lifespan)


def _history_items(messages: list) -> list[dict]:
    """把 LangGraph state 的历史 messages 转成前端可渲染的 item 列表。

    user / assistant 文本 + 工具调用（按 tool_call_id 配对其输出）。复用 TUI 的
    消息文本提取与可见性判断（懒加载，避免在 headless 服务启动时引入 textual）。
    """
    # message_restore 引入 textual，故延迟到此处导入而非模块顶层
    from lumi.tui.message_restore import (
        extract_human_display_text,
        extract_text_content,
    )
    from lumi.tui.message_visibility import should_show_human_message

    tool_outputs: dict[str, str] = {}
    for m in messages:
        if getattr(m, "type", None) == "tool":
            tool_outputs[getattr(m, "tool_call_id", "")] = extract_text_content(
                m.content
            )

    items: list[dict] = []
    for m in messages:
        kind = getattr(m, "type", None)
        if kind == "human":
            if not should_show_human_message(m):
                continue
            text = extract_human_display_text(m.content)
            if text:
                items.append({"kind": "user", "text": text})
        elif kind == "ai":
            text = extract_text_content(m.content)
            if text:
                items.append({"kind": "assistant", "text": text})
            for tc in getattr(m, "tool_calls", None) or []:
                tc_id = tc.get("id", "")
                items.append(
                    {
                        "kind": "tool",
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                        "tool_call_id": tc_id,
                        "output": tool_outputs.get(tc_id, ""),
                        "done": True,
                    }
                )
    return items


async def _run_stream(ws: WebSocket, bridge: AgentBridge, gen) -> dict:
    """迭代 BridgeEvent 流，逐条转 wire 推给客户端；session_id 取当前 thread。"""
    async for evt in gen:
        await ws.send_json(
            {
                "method": "event",
                "params": bridge_event_to_wire(evt, bridge.current_thread_id),
            }
        )
    return {"ok": True}


async def _list_sessions(bridge: AgentBridge, params: dict) -> dict:
    from lumi.tui.session_store import list_sessions

    sessions = await list_sessions(
        bridge.graph,
        current_thread_id="",
        workspace=get_workspace_dir(),
        limit=params.get("limit", 50),
    )
    meta = load_all()
    out = []
    for s in sessions:
        entry = meta.get(s.thread_id, {})
        out.append(
            {
                "thread_id": s.thread_id,
                "first_message": s.first_message,
                "title": entry.get("title", ""),
                "pinned": bool(entry.get("pinned", False)),
                "created_at": s.created_at.isoformat(),
                "message_count": s.message_count,
                "display_time": s.display_time,
            }
        )
    # 置顶项排到最前；list_sessions 已按时间降序，sort 稳定保留组内顺序
    out.sort(key=lambda x: not x["pinned"])
    return {"sessions": out}


async def _load_history(bridge: AgentBridge, params: dict) -> dict:
    thread_id = params.get("thread_id", "")
    if bridge.graph is None or not thread_id:
        return {"items": []}
    snap = await bridge.graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = (snap.values or {}).get("messages", [])
    return {"items": _history_items(messages)}


async def _dispatch(
    ws: WebSocket, bridge: AgentBridge, method: str, params: dict
) -> dict:
    """执行一个 RPC 方法。流式方法把 BridgeEvent 推给客户端，其余直接返回结果。"""
    if method == "send_message":
        return await _run_stream(
            ws,
            bridge,
            bridge.stream_response(
                params.get("content", ""),
                tool_mode=params.get("tool_mode", "default"),
                execution_mode=params.get("execution_mode", "normal"),
            ),
        )
    if method == "resume":
        return await _run_stream(ws, bridge, bridge.stream_resume(params.get("value")))
    if method == "list_sessions":
        return await _list_sessions(bridge, params)
    if method == "new_session":
        tid = generate_thread_id()
        bridge.switch_thread(tid)
        return {"thread_id": tid}
    if method == "switch_session":
        tid = params.get("thread_id", "")
        bridge.switch_thread(tid)
        return {"thread_id": tid}
    if method == "load_history":
        return await _load_history(bridge, params)
    if method == "pin_session":
        tid = params.get("thread_id", "")
        pinned = bool(params.get("pinned", False))
        update_meta(tid, pinned=pinned)
        return {"thread_id": tid, "pinned": pinned}
    if method == "rename_session":
        tid = params.get("thread_id", "")
        title = params.get("title", "")
        update_meta(tid, title=title)
        return {"thread_id": tid, "title": title}
    if method == "delete_session":
        tid = params.get("thread_id", "")
        await bridge.delete_thread(tid)
        delete_meta(tid)
        return {"thread_id": tid}
    raise ValueError(f"未知方法: {method}")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    bridge = AgentBridge()
    await bridge.initialize()
    await ws.send_json(
        {
            "method": "event",
            "params": {
                "type": "gateway.ready",
                "session_id": bridge.current_thread_id,
                "payload": {"model": bridge.model_name},
            },
        }
    )

    try:
        while True:
            frame = await ws.receive_json()
            rid = frame.get("id")
            method = frame.get("method", "")
            params = frame.get("params") or {}
            try:
                result = await _dispatch(ws, bridge, method, params)
                if rid is not None:
                    await ws.send_json({"id": rid, "result": result})
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error("[WS] 处理 %s 失败: %s", method, e, exc_info=True)
                if rid is not None:
                    await ws.send_json({"id": rid, "error": {"message": str(e)}})
    except WebSocketDisconnect:
        logger.info("[WS] 客户端断开: %s", bridge.current_thread_id)
    finally:
        await bridge.close()

"""Desktop WebSocket 服务：把 AgentBridge 暴露为 JSON-RPC over WS。

帧协议（client ↔ server）：
    client → server  {id, method, params}
        method=send_message  params: {content, tool_mode?, execution_mode?}
        method=resume        params: {value}   # 审批 / ask / plan 回传
    server → client
        事件帧  {method: "event", params: <wire event>}   # 见 protocol.py
        响应帧  {id, result}  或  {id, error: {message}}

一个 WS 连接 = 一个会话（独立 AgentBridge）。一次只跑一轮：run 进行时不读新帧，
中断（approval/clarify/plan）后回到接收循环等待 resume。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lumi.agents.bridge import AgentBridge
from lumi.server.protocol import bridge_event_to_wire
from lumi.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    from lumi.utils.patches import apply_all
    from lumi.utils.read_config import get_config

    apply_all()
    get_config().apply_env()
    yield


app = FastAPI(lifespan=lifespan)


async def _dispatch(
    ws: WebSocket, bridge: AgentBridge, session_id: str, method: str, params: dict
) -> dict:
    """执行一个 RPC 方法，把产生的 BridgeEvent 流式推给客户端。"""
    if method == "send_message":
        gen = bridge.stream_response(
            params.get("content", ""),
            tool_mode=params.get("tool_mode", "default"),
            execution_mode=params.get("execution_mode", "normal"),
        )
    elif method == "resume":
        gen = bridge.stream_resume(params.get("value"))
    else:
        raise ValueError(f"未知方法: {method}")

    async for evt in gen:
        await ws.send_json(
            {"method": "event", "params": bridge_event_to_wire(evt, session_id)}
        )
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    bridge = AgentBridge()
    await bridge.initialize()
    session_id = bridge.current_thread_id
    await ws.send_json(
        {
            "method": "event",
            "params": {
                "type": "gateway.ready",
                "session_id": session_id,
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
                result = await _dispatch(ws, bridge, session_id, method, params)
                if rid is not None:
                    await ws.send_json({"id": rid, "result": result})
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error("[WS] 处理 %s 失败: %s", method, e, exc_info=True)
                if rid is not None:
                    await ws.send_json({"id": rid, "error": {"message": str(e)}})
    except WebSocketDisconnect:
        logger.info("[WS] 客户端断开: %s", session_id)
    finally:
        await bridge.close()

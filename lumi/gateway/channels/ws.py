"""Desktop WebSocket 服务：把 GatewaySession 暴露为 JSON-RPC over WS。

帧协议（client ↔ server）：
    client → server  {id, method, params}
        send_message    params: {content, tool_mode?, execution_mode?}   → 流式
        resume          params: {value}                                   → 流式
        stop            params: {}                                        → {stopped}  # 中止当前流式轮
        list_commands   params: {}                                        → {commands:[...]}
        run_command     params: {name, extra_text?, tool_mode?}           → 流式
        list_providers  params: {}                                        → {profiles:[...], active:{provider,model}}
        test_provider   params: {base_url, api_key, model}                → {ok, error?, latency_ms?}
        set_provider    params: {provider, model}                         → {active:{provider,model}, model}
        save_provider   params: {profile}  # profile.models:[...]         → {profiles:[...], active}
        delete_provider params: {id}                                      → {profiles:[...], active}
        set_effort      params: {provider, model, level}                  → {effort}  # 档位 ∈ 该模型能力(models.dev)
        set_workspace   params: {path}                                    → {workspace}  # 进程级（切项目）
        list_projects   params: {}                                        → {projects:[...], current}
        add_project     params: {path}                                    → {projects:[...]}
        remove_project  params: {path}                                    → {projects:[...]}
        rename_project  params: {path, name}                              → {projects:[...]}
        add_folder      params: {path}                                    → {folders:[...]}  # 本会话临时
        remove_folder   params: {path}                                    → {folders:[...]}
        list_sessions   params: {limit?}                                  → {sessions:[...]}
        new_session     params: {}                                        → {thread_id}
        switch_session  params: {thread_id}                               → {thread_id}
        load_history    params: {thread_id}                               → {items:[...]}
        pin_session     params: {thread_id, pinned}                       → {thread_id, pinned}
        rename_session  params: {thread_id, title}                        → {thread_id, title}
        delete_session  params: {thread_id}                               → {thread_id}
        list_cron_jobs  params: {}                                        → {jobs:[...]}  # job 含 next_run
        create_cron_job params: {name, schedule, prompt}                  → {job}
        update_cron_job params: {job_id, name?, schedule?, prompt?}       → {job}
        delete_cron_job params: {job_id}                                  → {job_id}
        toggle_cron_job params: {job_id, enabled}                         → {job}
        run_cron_job    params: {job_id}                                  → {ok}  # 异步触发，结果经 cron.result
        list_cron_runs  params: {job_id, limit?}                          → {runs:[...]}
    server → client
        事件帧  {method: "event", params: <wire event>}   # 见 protocol.py
        响应帧  {id, result}  或  {id, error: {message}}

一个 WS 连接 = 一个 GatewaySession（独立 AgentBridge，可切换 thread）。本模块退化为
传输适配：把 fastapi WebSocket 包成 Channel（WsChannel），编排/分发/并发全在
GatewaySession（见 session.py）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lumi.gateway.bootstrap import gateway_process
from lumi.gateway.bridge import AgentBridge
from lumi.gateway.broadcast import hub
from lumi.gateway.session import GatewaySession
from lumi.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 进程级 bootstrap 与所有 channel 共享，见 gateway/bootstrap.py
    async with gateway_process():
        yield


app = FastAPI(lifespan=lifespan)


class WsChannel:
    """把 fastapi WebSocket 适配为 Channel：send 即 send_json。"""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send(self, frame: dict) -> None:
        await self._ws.send_json(frame)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    bridge = AgentBridge()
    await bridge.initialize()
    session = GatewaySession(bridge, WsChannel(ws), hub)
    await session.start()
    try:
        while True:
            await session.handle_frame(await ws.receive_json())
    except WebSocketDisconnect:
        logger.info("[WS] 客户端断开: %s", bridge.current_thread_id)
    finally:
        await session.aclose()

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
        set_workspace   params: {path}                                    → {workspace}  # 会话级（绑定本连接项目，不动进程 cwd）
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

一个 WS 连接 = 一个 GatewaySession（独立 AgentBridge，可切换 thread）。连接 URL 可带
``?token=``（鉴权）与 ``?workspace=``（本会话项目，open 时直接 pin 引擎）。本模块退化为
传输适配：把 fastapi WebSocket 包成 Channel（WsChannel），编排/分发/并发全在
GatewaySession（见 session.py）。
"""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lumi.gateway.bootstrap import gateway_process
from lumi.gateway.bridge import AgentBridge
from lumi.gateway.broadcast import hub
from lumi.gateway.session import GatewaySession
from lumi.gateway.session_registry import registry
from lumi.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 进程级 bootstrap 与所有 channel 共享，见 gateway/bootstrap.py
    async with gateway_process():
        yield


app = FastAPI(lifespan=lifespan)


def token_ok(configured: str, provided: str | None) -> bool:
    """鉴权：未配置 token（空串）则放行；配置了则需精确匹配（防时序攻击）。

    token 由 `lumi serve --token` 设到 app.state，客户端经 `?token=` 携带。
    本地 sidecar 与远程公网部署走同一套，无"本地免鉴权"特例。
    """
    if not configured:
        return True
    return provided is not None and hmac.compare_digest(configured, provided)


class WsChannel:
    """把 fastapi WebSocket 适配为 Channel：send 即 send_json。"""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send(self, frame: dict) -> None:
        await self._ws.send_json(frame)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # 先 accept 再校验：accept 前 close 浏览器只见握手失败(1006)，无法区分鉴权/不可达；
    # accept 后以 1008 关闭，客户端能拿到干净的 close code 来分辨「token 无效」。
    await ws.accept()
    if not token_ok(getattr(app.state, "token", ""), ws.query_params.get("token")):
        await ws.close(code=1008)
        return
    ch = WsChannel(ws)
    # 断连续接（Case 1）：URL 带 ?thread= 且该 thread 有「断开但仍挂着活跃轮」的 detached
    # 会话 → 接回复用（parked turn / broker / 挂起审批原样还在），否则照旧新建 bridge。
    thread = ws.query_params.get("thread", "")
    session = registry.take(thread) if thread else None
    if session is not None:
        await session.reattach(ch)
    else:
        bridge = AgentBridge()
        # open 握手携带 ?workspace=：直接把本会话引擎 pin 到其项目（项目随会话绑定），
        # 省掉 ready 后再 switch_session rebase 的来回。缺省 / 无效则退回进程 cwd。
        await bridge.initialize(project_dir=ws.query_params.get("workspace", ""))
        session = GatewaySession(bridge, ch, hub)
        await session.start()
    try:
        while True:
            await session.handle_frame(await ws.receive_json())
    except WebSocketDisconnect:
        logger.info("[WS] 客户端断开: %s", session.current_thread_id)
    finally:
        # 值得续接（有活跃用户轮，纯后台 meta 轮除外）→ detach 留存待同 thread 重连；
        # 否则正常收尾
        if session.should_detach():
            displaced = session.detach(registry)
            if displaced is not None:
                await displaced.aclose()
        else:
            await session.aclose()

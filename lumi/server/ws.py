"""Desktop WebSocket 服务：把 AgentBridge 暴露为 JSON-RPC over WS。

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

一个 WS 连接 = 一个会话上下文（独立 AgentBridge，可切换 thread）。同一时刻只跑一
轮用户流式响应，但该轮在独立 task 中执行，主循环持续读帧，故运行期间仍可接收 stop
将其取消；中断（approval/clarify/plan）后回到接收循环等待 resume。

非流式 RPC 同样 spawn 成独立 task：部分方法需等待 run.lock（与流式轮互斥），
inline await 会卡住接收循环，使 stop 帧在整轮结束前都读不到。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lumi.agents.bridge import AgentBridge, EventKind, shutdown_shared_runtime
from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.runtime import setup_cron
from lumi.server.cron_rpc import CRON_METHODS, dispatch_cron, set_cron_runtime
from lumi.server.desktop_delivery import DesktopDelivery
from lumi.server.projects import (
    add_project,
    list_projects,
    remove_project,
    rename_project,
    touch_project,
)
from lumi.server.protocol import bridge_event_to_wire, event_frame
from lumi.tui.session_meta import delete_meta, load_all, update_meta
from lumi.utils.constants import NOTIFICATION_POLL_INTERVAL
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id
from lumi.utils.workspace_id import get_workspace_dir

# 流以中断事件收尾 → 该轮尚未结束，正等待客户端 resume，期间不可插入后台通知轮
_INTERRUPT_KINDS = frozenset({EventKind.CLARIFY, EventKind.APPROVAL, EventKind.PLAN})


# 进程级 cron 结果广播通道：连接建立/断开时在 endpoint 注册/注销
_desktop_delivery = DesktopDelivery()


# 事件循环只弱引用 task，不自持引用的话广播 task 可能在执行前被 GC
_broadcast_tasks: set[asyncio.Task] = set()


def _spawn_broadcast(coro) -> None:
    """fire-and-forget 一个广播协程，自持引用避免执行前被 GC（cron / bg_tasks 共用）。"""
    task = asyncio.create_task(coro)
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


def _on_cron_job_status(names: list[str]) -> None:
    """Scheduler 同步回调：把运行中任务名列表广播为 cron.running 事件。"""
    _spawn_broadcast(_desktop_delivery.send_event("cron.running", {"names": names}))


def _serialize_bg_tasks() -> list[dict]:
    from lumi.agents.runtime.bg_tasks import get_task_registry, serialize_task

    return [serialize_task(e) for e in get_task_registry().all_tasks()]


# 后台任务广播去抖：workflow 扇出时 notify_progress 高频触发，~100ms 内的多次变更
# 合并为一次全量快照广播（最终态必发，见 _bg_flush 尾部的脏标志补发）。
_bg_dirty = False
_bg_flush_scheduled = False


def _on_bg_task_change() -> None:
    """TaskRegistry 同步回调：标脏并安排一次去抖广播（携带全量快照，前端按 thread 过滤）。"""
    global _bg_dirty
    _bg_dirty = True
    _schedule_bg_flush()


def _schedule_bg_flush() -> None:
    global _bg_flush_scheduled
    if _bg_flush_scheduled:
        return
    _bg_flush_scheduled = True
    _spawn_broadcast(_bg_flush())


async def _bg_flush() -> None:
    global _bg_dirty, _bg_flush_scheduled
    try:
        await asyncio.sleep(0.1)  # 合并窗口
        _bg_dirty = False
        await _desktop_delivery.send_event(
            "bg_tasks.update", {"tasks": _serialize_bg_tasks()}
        )
    finally:
        _bg_flush_scheduled = False
    if _bg_dirty:  # 窗口内又有新变更 → 补发一次，保证最终态送达
        _schedule_bg_flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from lumi.utils.patches import apply_all
    from lumi.utils.read_config import get_config

    apply_all()
    get_config().apply_env()

    # 后台刷新 models.dev 模型目录（思考能力 + context_length 数据源）
    from lumi.utils.model_catalog import refresh as refresh_catalog

    asyncio.create_task(refresh_catalog())

    # 初始化定时任务子系统（按工作目录隔离，与 TUI 共用 setup_cron）
    cron_runtime = None
    try:
        delivery = DeliveryManager()
        delivery.register(_desktop_delivery)
        cron_runtime = setup_cron(delivery, on_job_status=_on_cron_job_status)
        set_cron_runtime(cron_runtime)
        await cron_runtime.scheduler.start()
        logger.info("[WS] 定时任务子系统已启动")
    except Exception:
        logger.warning("[WS] 定时任务子系统启动失败，cron 功能不可用", exc_info=True)

    # 后台任务变更 → 广播 bg_tasks.update，驱动 desktop drawer 实时刷新
    from lumi.agents.runtime.bg_tasks import get_task_registry

    get_task_registry().set_on_change(_on_bg_task_change)

    yield

    get_task_registry().set_on_change(None)
    if cron_runtime is not None:
        await cron_runtime.scheduler.stop()
    # 进程级共享运行时（MCP / shell 会话）只在进程退出时关闭一次，
    # 不能随单条连接的 bridge.close() 拆除
    await shutdown_shared_runtime()


app = FastAPI(lifespan=lifespan)


def _extract_images(content) -> list[str]:
    """从 HumanMessage 的 list content 提取图片块，转回前端可渲染的 data URL。

    后端图片块为 Anthropic 原生格式 {type:image, source:{type:base64, media_type, data}}，
    前端 <img> 需要 data URL，故拼回 data:{media_type};base64,{data}。
    """
    if not isinstance(content, list):
        return []
    urls: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            src = block.get("source", {})
            if src.get("type") == "base64" and src.get("data"):
                media = src.get("media_type", "image/png")
                urls.append(f"data:{media};base64,{src['data']}")
    return urls


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
            images = _extract_images(m.content)
            if text or images:
                item = {"kind": "user", "text": text}
                if images:
                    item["images"] = images
                items.append(item)
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


@dataclass
class _RunState:
    """单条 WS 连接的运行协调状态。

    lock 串行化所有会改写 bridge 运行态的操作（用户轮 / 后台通知轮 / 切换会话），
    确保同一时刻 bridge 上只跑一件事。awaiting_resume 标记上一轮以中断收尾、正等待
    客户端 resume，此期间后台通知轮不得插入（否则会破坏挂起的中断状态）。
    task 持有当前正在跑的用户流式轮——独立于主接收循环，以便 stop 帧能取消它。
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    awaiting_resume: bool = False
    task: "asyncio.Task | None" = None


async def _pump(ws: WebSocket, bridge: AgentBridge, run: _RunState, gen) -> dict:
    """迭代 BridgeEvent 流逐条转 wire 推给客户端（假定已持有 run.lock）。

    依据最后一个事件是否为中断更新 awaiting_resume：以中断收尾 → 等待 resume。
    """
    last_kind = None
    async for evt in gen:
        last_kind = evt.kind
        await ws.send_json(bridge_event_to_wire(evt, bridge.current_thread_id))
    run.awaiting_resume = last_kind in _INTERRUPT_KINDS
    return {"ok": True}


async def _run_stream(ws: WebSocket, bridge: AgentBridge, run: _RunState, gen) -> dict:
    """串行化地跑一轮事件流（用户消息 / resume / 命令）。"""
    async with run.lock:
        return await _pump(ws, bridge, run, gen)


# 需后台 task 承载、可被 stop 取消的流式方法
_STREAMING_METHODS = frozenset({"send_message", "resume", "run_command"})


def _stream_gen(bridge: AgentBridge, method: str, params: dict):
    """为流式方法构造对应的 BridgeEvent 异步生成器（不在此处启动迭代）。"""
    if method == "send_message":
        return bridge.stream_response(
            params.get("content", ""),
            tool_mode=params.get("tool_mode", "default"),
            execution_mode=params.get("execution_mode", "normal"),
        )
    if method == "resume":
        return bridge.stream_resume(params.get("value"))
    return bridge.stream_command(
        params.get("name", ""),
        extra_text=params.get("extra_text", ""),
        tool_mode=params.get("tool_mode", "default"),
    )


async def _run_streaming_rpc(
    ws: WebSocket, bridge: AgentBridge, run: _RunState, rid, gen
) -> None:
    """在独立 task 里跑一轮流式响应；被 stop 取消时给前端补发 turn.complete 收尾。

    放到 task 中（而非主循环内 await）是为了让主循环能在运行期间继续读帧、
    收到 stop 后取消本 task。
    """
    try:
        result = await _run_stream(ws, bridge, run, gen)
        if rid is not None:
            await ws.send_json({"id": rid, "result": result})
    except asyncio.CancelledError:
        # 被 stop 取消：本轮作废，通知前端结束 running 态（吞掉取消，已妥善收尾）
        run.awaiting_resume = False
        with suppress(Exception):
            await ws.send_json(
                event_frame(str(EventKind.TURN_COMPLETE), bridge.current_thread_id, {})
            )
        if rid is not None:
            with suppress(Exception):
                await ws.send_json({"id": rid, "result": {"stopped": True}})
    except Exception as e:
        logger.error("[WS] 流式任务失败: %s", e, exc_info=True)
        if rid is not None:
            with suppress(Exception):
                await ws.send_json({"id": rid, "error": {"message": str(e)}})
    finally:
        run.task = None


async def _notification_loop(
    ws: WebSocket, bridge: AgentBridge, run: _RunState
) -> None:
    """后台任务完成通知轮询（对齐 TUI 的 _poll_notifications）。

    Agent 空闲时取出通知队列，作为不可见 meta 消息注入触发新一轮，让模型读取输出
    文件并把结果主动流式推回 desktop——否则通知只会堆积在队列里无人取用，桌面端
    永远收不到后台任务的完成反馈。
    """
    while True:
        await asyncio.sleep(NOTIFICATION_POLL_INTERVAL)
        # 队列空（绝大多数 tick）时不去抢 run.lock，避免在流式轮后面排队
        if run.awaiting_resume or not bridge.has_notifications():
            continue
        async with run.lock:
            # 抢到锁后复检：等锁期间用户轮可能刚以中断收尾
            if run.awaiting_resume:
                continue
            # 只认领归属本连接当前 thread 的通知——队列是进程级共享的，
            # drain_all 会把其他会话的后台任务通知抢到本会话注入
            hint = bridge.drain_notification_hint(bridge.current_thread_id)
            if not hint:
                continue
            logger.info("[WS] 注入后台任务通知")
            try:
                await _pump(
                    ws,
                    bridge,
                    run,
                    bridge.stream_response(hint, tool_mode="default", is_meta=True),
                )
            except Exception:
                # 连接断裂等：主循环会随之收尾并取消本任务，这里仅记录不致命
                logger.error("[WS] 后台通知轮执行失败", exc_info=True)


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


async def _stop(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    # 中止当前用户轮：取消 task，其取消处理会补发 turn.complete 收尾
    task = run.task
    if task is not None and not task.done():
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}


async def _list_commands(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return {"commands": bridge.list_commands()}


async def _list_providers(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return bridge.list_providers()


async def _test_provider(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return await bridge.test_provider(
        params.get("base_url", ""),
        params.get("api_key", ""),
        params.get("model", ""),
    )


# provider 变更经 _apply_active 改写运行时 context，须与运行中的轮次互斥，
# 否则会在轮内改掉下一次 call_model 读取的共享 context（中途换模型/连接）。
async def _set_provider(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        return bridge.set_provider(params.get("provider", ""), params.get("model", ""))


async def _save_provider(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        return bridge.save_provider(params.get("profile", {}))


async def _delete_provider(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        return bridge.delete_provider(params.get("id", ""))


async def _set_effort(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return bridge.set_effort(
        params.get("provider", ""), params.get("model", ""), params.get("level", "")
    )


# chdir / 权限边界 / shell 会话都是进程级状态，须与运行中的轮次互斥
async def _set_workspace(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        result = await bridge.set_workspace(params.get("path", ""))
    touch_project(result["workspace"])
    return result


async def _list_projects(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return {"projects": list_projects(), "current": get_workspace_dir()}


async def _add_project(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return {"projects": add_project(params.get("path", ""), params.get("name", ""))}


async def _remove_project(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return {"projects": remove_project(params.get("path", ""))}


async def _rename_project(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return {"projects": rename_project(params.get("path", ""), params.get("name", ""))}


# 改写本连接 engine 的边界，与运行中的轮次互斥
async def _add_folder(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        return bridge.add_folder(params.get("path", ""))


async def _remove_folder(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    async with run.lock:
        return bridge.remove_folder(params.get("path", ""))


async def _switch_session(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    # new_session 不带 thread_id → 生成新的。切 thread 会改写 bridge._config，
    # 须与运行中的轮次互斥
    tid = params.get("thread_id") or generate_thread_id()
    async with run.lock:
        bridge.switch_thread(tid)
        run.awaiting_resume = False
    return {"thread_id": tid}


async def _pin_session(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    tid = params.get("thread_id", "")
    pinned = bool(params.get("pinned", False))
    update_meta(tid, pinned=pinned)
    return {"thread_id": tid, "pinned": pinned}


async def _rename_session(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    tid = params.get("thread_id", "")
    title = params.get("title", "")
    update_meta(tid, title=title)
    return {"thread_id": tid, "title": title}


async def _delete_session(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    tid = params.get("thread_id", "")
    async with run.lock:
        await bridge.delete_thread(tid)
    delete_meta(tid)
    return {"thread_id": tid}


async def _list_sessions_rpc(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return await _list_sessions(bridge, params)


async def _load_history_rpc(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    return await _load_history(bridge, params)


async def _list_bg_tasks(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    """全部后台任务快照（前端按当前 thread_id 过滤）。"""
    return {"tasks": _serialize_bg_tasks()}


def _owns_bg_task(bridge: AgentBridge, task_id: str) -> bool:
    """任务是否属于本连接当前会话（防跨会话 stop/dismiss）。

    不存在 → True（交下游返回未停止/未移除）；归属为空 → True（无主任务任一会话可清）。
    """
    from lumi.agents.runtime.bg_tasks import get_task_registry

    entry = get_task_registry().get(task_id)
    return (
        entry is None
        or not entry.thread_id
        or entry.thread_id == bridge.current_thread_id
    )


async def _stop_bg_task(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    """停止运行中的后台任务（drawer 停止按钮）；仅限本会话任务。"""
    from lumi.agents.runtime.session import cancel_background_task

    task_id = params.get("task_id", "")
    if not _owns_bg_task(bridge, task_id):
        return {"stopped": False, "error": "任务不属于当前会话"}
    return {"stopped": await cancel_background_task(task_id)}


async def _dismiss_bg_task(bridge: AgentBridge, run: _RunState, params: dict) -> dict:
    """从列表移除一个终态后台任务（drawer 移除 ✕）；仅限本会话任务。"""
    from lumi.agents.runtime.bg_tasks import get_task_registry

    task_id = params.get("task_id", "")
    if not _owns_bg_task(bridge, task_id):
        return {"dismissed": False}
    return {"dismissed": get_task_registry().dismiss(task_id)}


async def _clear_finished_bg_tasks(
    bridge: AgentBridge, run: _RunState, params: dict
) -> dict:
    """清除当前会话的全部终态后台任务（drawer 头部「清除已完成」）。"""
    from lumi.agents.runtime.bg_tasks import get_task_registry

    return {"cleared": get_task_registry().clear_finished(bridge.current_thread_id)}


# 非流式 RPC 分发表。契约测试从 IMPLEMENTED_METHODS 读取实现的方法集合，
# 新增方法只需在此登记 + events.json 声明，漂移会被测试直接抓住。
_RPC_HANDLERS = {
    "stop": _stop,
    "list_commands": _list_commands,
    "list_providers": _list_providers,
    "test_provider": _test_provider,
    "set_provider": _set_provider,
    "save_provider": _save_provider,
    "delete_provider": _delete_provider,
    "set_effort": _set_effort,
    "set_workspace": _set_workspace,
    "list_projects": _list_projects,
    "add_project": _add_project,
    "remove_project": _remove_project,
    "rename_project": _rename_project,
    "add_folder": _add_folder,
    "remove_folder": _remove_folder,
    "list_sessions": _list_sessions_rpc,
    "new_session": _switch_session,
    "switch_session": _switch_session,
    "load_history": _load_history_rpc,
    "pin_session": _pin_session,
    "rename_session": _rename_session,
    "delete_session": _delete_session,
    "list_bg_tasks": _list_bg_tasks,
    "stop_bg_task": _stop_bg_task,
    "dismiss_bg_task": _dismiss_bg_task,
    "clear_finished_bg_tasks": _clear_finished_bg_tasks,
}

# 本服务实现的全部 RPC 方法（供协议契约测试断言与 events.json 一致）
IMPLEMENTED_METHODS = frozenset(_RPC_HANDLERS) | _STREAMING_METHODS | CRON_METHODS


async def _dispatch(
    ws: WebSocket, bridge: AgentBridge, run: _RunState, method: str, params: dict
) -> dict:
    """执行一个非流式 RPC 方法并返回结果（在独立 task 中运行，见 _run_rpc）。

    流式方法（send_message / resume / run_command）不走这里，由 endpoint 单独
    spawn 成可取消的 task（见 _run_streaming_rpc）。
    """
    handler = _RPC_HANDLERS.get(method)
    if handler is not None:
        return await handler(bridge, run, params)
    if method in CRON_METHODS:
        return await dispatch_cron(method, params)
    raise ValueError(f"未知方法: {method}")


async def _run_rpc(
    ws: WebSocket, bridge: AgentBridge, run: _RunState, rid, method: str, params: dict
) -> None:
    """在独立 task 中执行非流式 RPC 并回发响应帧。

    需要 run.lock 的方法（delete_session / set_provider 等）在流式轮进行中
    会等锁——若 inline await 在接收循环里，等锁期间连 stop 帧都读不到。
    """
    try:
        result = await _dispatch(ws, bridge, run, method, params)
        if rid is not None:
            await ws.send_json({"id": rid, "result": result})
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[WS] 处理 %s 失败: %s", method, e, exc_info=True)
        if rid is not None:
            with suppress(Exception):
                await ws.send_json({"id": rid, "error": {"message": str(e)}})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    bridge = AgentBridge()
    await bridge.initialize()
    await ws.send_json(
        event_frame(
            "gateway.ready",
            bridge.current_thread_id,
            {"model": bridge.model_name, "workspace": get_workspace_dir()},
        )
    )

    run = _RunState()
    # 注册到 cron 结果广播通道：任务完成/运行状态变化实时推给本连接
    _desktop_delivery.register_ws(ws)
    # 后台任务完成通知轮询：与主接收循环并发，空闲时把队列通知注入新一轮推回前端
    notif_task = asyncio.create_task(_notification_loop(ws, bridge, run))
    rpc_tasks: set[asyncio.Task] = set()

    try:
        while True:
            frame = await ws.receive_json()
            rid = frame.get("id")
            method = frame.get("method", "")
            params = frame.get("params") or {}

            # 流式方法 spawn 成独立 task：主循环立即回到读帧，使运行期间仍能收到 stop
            if method in _STREAMING_METHODS:
                if run.task is not None and not run.task.done():
                    if rid is not None:
                        await ws.send_json(
                            {"id": rid, "error": {"message": "已有任务在执行"}}
                        )
                    continue
                run.task = asyncio.create_task(
                    _run_streaming_rpc(
                        ws, bridge, run, rid, _stream_gen(bridge, method, params)
                    )
                )
                continue

            task = asyncio.create_task(_run_rpc(ws, bridge, run, rid, method, params))
            rpc_tasks.add(task)
            task.add_done_callback(rpc_tasks.discard)
    except WebSocketDisconnect:
        logger.info("[WS] 客户端断开: %s", bridge.current_thread_id)
    finally:
        _desktop_delivery.unregister_ws(ws)
        notif_task.cancel()
        with suppress(asyncio.CancelledError):
            await notif_task
        for task in (*rpc_tasks, run.task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        await bridge.close()

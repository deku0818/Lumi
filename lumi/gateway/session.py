"""GatewaySession：传输无关的会话编排层。

吸收原 WS 端点里的全部连接编排 / RPC 分发 / 并发协调 / 后台通知逻辑，只通过
``Channel`` 协议向前端推帧。一条连接（WS 或未来 IM）= 一个 GatewaySession，
持有独立的 AgentBridge（可切换 thread），同一时刻只跑一轮用户流式响应，但该轮在
独立 task 中执行，handle_frame 持续读帧，故运行期间仍可接收 stop 将其取消；中断
（approval/clarify/plan）后回到接收循环等待 resume。

非流式 RPC 同样 spawn 成独立 task：部分方法需等待 run.lock（与流式轮互斥），
inline await 会卡住接收循环，使 stop 帧在整轮结束前都读不到。

帧协议（client ↔ server）见 channels/ws.py 模块文档。
"""

from __future__ import annotations

import asyncio
import ntpath
import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lumi.agents.core.meta_message import declared_items
from lumi.gateway.bridge import AgentBridge, EventKind
from lumi.gateway.broadcast import BroadcastHub, serialize_bg_tasks
from lumi.gateway.channel import Channel
from lumi.gateway.channel_rpc import CHANNEL_METHODS, dispatch_channel
from lumi.gateway.channels.manager import manager
from lumi.gateway.cron_rpc import CRON_METHODS, dispatch_cron
from lumi.gateway.mcp_rpc import MCP_METHODS, dispatch_mcp
from lumi.gateway.projects import (
    add_project,
    list_projects,
    remove_project,
    rename_project,
    set_default_project,
    touch_project,
)
from lumi.gateway.protocol import bridge_event_to_wire, event_frame
from lumi.sessions.message_text import extract_text_content, visible_user_text
from lumi.sessions.message_visibility import should_show_human_message
from lumi.sessions.session_meta import delete_meta, load_all, update_meta
from lumi.sessions.session_store import list_sessions
from lumi.utils.constants import (
    FEISHU_THREAD_PREFIX,
    NOTIFICATION_POLL_INTERVAL,
)
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id

if TYPE_CHECKING:
    from lumi.gateway.session_registry import SessionRegistry

# 需后台 task 承载、可被 stop 取消的流式方法。resume 改非流式控制 RPC（在途审批应答）：
# 审批挂起期间原 prompt 流仍活、run.lock 仍持，流式方法会被拒成「已有任务在执行」。
_STREAMING_METHODS = frozenset({"send_message", "run_command"})

# 断连续接（Case 1）：detached 会话无人接回的兜底回收时长（保底防进程内泄漏）
_DETACH_TTL_SECONDS = 8 * 3600
_WINDOWS_ROOTS_PATH = "__lumi_windows_roots__"


def _windows_drive_roots() -> list[str]:
    return sorted(d for d in os.listdrives() if os.path.isdir(d))


def _parent_for_list_dir(path: str) -> str | None:
    parent = ntpath.dirname(path) if os.name == "nt" else os.path.dirname(path)
    if parent != path:
        return parent
    # 已在某个根：Windows 盘符根 → 虚拟「此电脑」；UNC 根 / POSIX / 无上级
    if os.name == "nt" and len(ntpath.splitdrive(path)[0]) == 2:
        return _WINDOWS_ROOTS_PATH
    return None


def _channel_of(thread_id: str) -> str:
    """thread → 所属 IM 渠道（thread 前缀是渠道的确定性派生，见 feishu_thread_id）。

    会话列表标注与只读守卫共用此单一判定；desktop 端只消费 wire 的 channel 字段。
    """
    return "feishu" if thread_id.startswith(FEISHU_THREAD_PREFIX) else ""


class _NoopChannel:
    """detached 期的丢弃式 channel：会话原地挂着但暂无 WS，事件落地即弃——重连靠
    pending_approval_events 重发挂起卡片 + load_history 对账补回 gap 期消息。"""

    async def send(self, frame: dict) -> None:
        return


def _user_items(m) -> list[dict]:
    """一条 HumanMessage → 前端 user item 列表（声明的纯投影）。

    渲染数据全部来自显示声明（构造时写好的 sender/ts/text/files，含消息级 ts
    的单条下沉——规则在写侧 _build_user_message），不解析正文——正文里的标签
    纯给模型看。未声明的消息 fallback 到 visible_user_text；cron 的任务 prompt
    经 synthetic_human_message 声明 items:[]（刻意不显示，prompt 见任务详情页）。
    """
    out: list[dict] = []
    for it in declared_items(m) or []:
        item = {"kind": "user", "text": it.get("text", "")}
        for key in ("sender", "ts", "files"):
            if it.get(key):
                item[key] = it[key]
        out.append(item)
    if not out:
        text = visible_user_text(m)
        if text:
            out.append({"kind": "user", "text": text})
    return out


def _history_items(messages: list) -> list[dict]:
    """把 LangGraph state 的历史 messages 转成前端可渲染的 item 列表。

    user / assistant 文本 + 工具调用（按 tool_call_id 配对其输出）。消息文本提取与
    可见性判断复用 lumi.sessions 下的纯逻辑。
    """
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
            if should_show_human_message(m):
                items.extend(_user_items(m))
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
    """单条连接的运行协调状态。

    lock 串行化所有会改写 bridge 运行态的操作（用户轮 / 后台通知轮 / 切换会话），
    确保同一时刻 bridge 上只跑一件事。在途审批期间该轮仍活、持着 lock，后台通知轮抢锁
    自然被挡，无需额外旗标。task 持有当前正在跑的用户流式轮——独立于主接收循环，以便
    stop 帧能取消它。
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None


async def _list_sessions(bridge: AgentBridge, params: dict) -> dict:
    # workspace="" → 跨所有项目列出（方案甲机器→项目分组树由前端按 workspace_dir 分组）；
    # 不再按当前进程 cwd 过滤，故切项目不影响列表完整性。
    sessions = await list_sessions(
        bridge.graph,
        current_thread_id="",
        workspace="",
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
                # 手动重命名 > 渠道自动名（飞书群名/私聊对方姓名）> 自动生成标题
                "title": entry.get("title")
                or entry.get("channel_title")
                or entry.get("auto_title", ""),
                "pinned": bool(entry.get("pinned", False)),
                "created_at": s.created_at.isoformat(),
                "message_count": s.message_count,
                "display_time": s.display_time,
                "workspace_dir": s.workspace_dir,
                # IM 渠道会话标识：前端据此分组/挂徽标；kind 区分群聊与私聊图标
                "channel": _channel_of(s.thread_id),
                "channel_kind": entry.get("channel_kind", ""),
            }
        )
    # 置顶项排到最前；list_sessions 已按时间降序，sort 稳定保留组内顺序
    out.sort(key=lambda x: not x["pinned"])
    return {"sessions": out}


def _snapshot_model_window(messages: list) -> tuple[str, int]:
    """从末条带模型标记的 AI message 取会话真实模型名及其上下文窗口。

    渠道（飞书等）旁观会话在 desktop 无对应 activeModel，上下文环的分母不能用
    desktop 当前选中模型的窗口（会算错百分比），须取会话实际所跑模型——response_metadata
    的 model_name 是权威来源，再经 catalog 查目录得窗口。未知模型返回窗口 0（前端自会隐藏环）。
    """
    from lumi.models.catalog import lookup

    for msg in reversed(messages):
        model = (getattr(msg, "response_metadata", None) or {}).get("model_name")
        if model:
            entry = lookup(model)
            return model, (entry.context_length if entry else 0)
    return "", 0


async def _load_history(bridge: AgentBridge, params: dict) -> dict:
    thread_id = params.get("thread_id", "")
    if bridge.graph is None or not thread_id:
        return {"items": []}
    snap = await bridge.graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = (snap.values or {}).get("messages", [])
    # usage 随 items 回给前端还原上下文用量指示器（仅存前端内存，重启/切会话后本是空的）；
    # 复用 bridge 末条 AI usage 提取，口径与流式一致。model/context_window 供渠道旁观会话
    # 画上下文环——旁观视图无本地 activeModel，分母须由会话真实模型决定。
    model, context_window = _snapshot_model_window(messages)
    return {
        "items": _history_items(messages),
        "usage": AgentBridge._extract_last_ai_usage(snap),
        "model": model,
        "context_window": context_window,
    }


async def _stop(session: GatewaySession, params: dict) -> dict:
    # 中止当前用户轮：优先把挂起审批以拒绝收尾（保留历史），无挂起审批才硬取消 task。
    # 不等 task 结束，快速回应（取消处理自会补发 turn.complete 收尾）。
    return {"stopped": await session._finalize_active_turn(wait=False)}


async def _resume(session: GatewaySession, params: dict) -> dict:
    """在途审批应答（非流式控制 RPC）：唤醒挂起的审批 / 提问 Future。

    原 prompt 流仍活、run.lock 仍持，故应答走轻量 RPC 而非流式——事件继续从原流吐出，
    不开新流。value 形状沿用原 resume 值：tool_approval 为 {decision, message?,
    set_tool_mode?}；ask 为答案字符串 / ASK_CANCELLED。resolved=False 表示审批已被
    stop / 切会话作废（无未决请求命中）。
    """
    ok = session._bridge.resolve_approval(
        params.get("approval_id", ""), params.get("value")
    )
    return {"resolved": ok}


async def _list_commands(session: GatewaySession, params: dict) -> dict:
    return {"commands": session._bridge.list_commands()}


async def _list_providers(session: GatewaySession, params: dict) -> dict:
    return session._bridge.list_providers()


async def _test_provider(session: GatewaySession, params: dict) -> dict:
    return await session._bridge.test_provider(
        params.get("base_url", ""),
        params.get("api_key", ""),
        params.get("model", ""),
    )


# provider 变更经 _apply_active 改写运行时 context，须与运行中的轮次互斥，
# 否则会在轮内改掉下一次 call_model 读取的共享 context（中途换模型/连接）。
async def _set_provider(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.set_provider(
            params.get("provider", ""), params.get("model", "")
        )


async def _save_provider(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.save_provider(params.get("profile", {}))


async def _delete_provider(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.delete_provider(params.get("id", ""))


async def _set_effort(session: GatewaySession, params: dict) -> dict:
    # 与其余 provider 写操作一致持锁：set_effort 也走 provider_store load→改→save，
    # 不持锁会与并发的 set/save/delete_provider 互相 clobber（读改写丢更新）。
    async with session._run.lock:
        return session._bridge.set_effort(
            params.get("provider", ""), params.get("model", ""), params.get("level", "")
        )


async def _set_classifier(session: GatewaySession, params: dict) -> dict:
    # 同样走 provider_store load→改→save，持锁防与并发 provider 写操作 clobber。
    async with session._run.lock:
        return session._bridge.set_classifier(
            params.get("provider", ""), params.get("model", "")
        )


async def _set_titler(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.set_titler(
            params.get("provider", ""), params.get("model", "")
        )


# 刻意不持 _run.lock：与 set_provider 相反，这里就是要在运行中改共享 context 的
# tool_mode——单字段幂等赋值，只影响后续 is_use_tool 路由判决，实时切换正是需求本身。
async def _set_tool_mode(session: GatewaySession, params: dict) -> dict:
    return session._bridge.set_tool_mode(params.get("tool_mode", "default"))


# chdir / 权限边界 / shell 会话都是进程级状态，须与运行中的轮次互斥
async def _set_workspace(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        result = await session._bridge.set_workspace(params.get("path", ""))
    touch_project(result["workspace"])
    return result


async def _list_projects(session: GatewaySession, params: dict) -> dict:
    # current = 本会话项目（随会话绑定），而非进程 cwd
    return {"projects": list_projects(), "current": session._bridge.workspace_dir}


async def _add_project(session: GatewaySession, params: dict) -> dict:
    return {"projects": add_project(params.get("path", ""), params.get("name", ""))}


async def _remove_project(session: GatewaySession, params: dict) -> dict:
    return {"projects": remove_project(params.get("path", ""))}


async def _rename_project(session: GatewaySession, params: dict) -> dict:
    return {"projects": rename_project(params.get("path", ""), params.get("name", ""))}


async def _set_default_project(session: GatewaySession, params: dict) -> dict:
    return {
        "projects": set_default_project(
            params.get("path", ""), bool(params.get("default"))
        )
    }


# 远程目录浏览器：在「本后端」文件系统上浏览/建目录。前端按机器经各自控制连接调用，
# 故对远程机器即浏览远程文件系统（创建远程项目时选/建目录用）。
async def _list_dir(session: GatewaySession, params: dict) -> dict:
    raw = params.get("path")
    if os.name == "nt" and raw == _WINDOWS_ROOTS_PATH:
        return {
            "path": "",
            "parent": None,
            "dirs": _windows_drive_roots(),
            "selectable": False,
        }
    raw = raw or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(raw))
    try:
        dirs = sorted(
            e.name
            for e in os.scandir(path)
            if e.is_dir() and not e.name.startswith(".")
        )
    except OSError:
        dirs = []
    return {
        "path": path,
        "parent": _parent_for_list_dir(path),
        "dirs": dirs,
        "selectable": True,
    }


async def _make_dir(session: GatewaySession, params: dict) -> dict:
    path = os.path.abspath(os.path.expanduser(params.get("path", "")))
    try:
        os.makedirs(path, exist_ok=True)
        return {"ok": True, "path": path}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# 改写本连接 engine 的边界，与运行中的轮次互斥
async def _add_folder(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.add_folder(params.get("path", ""))


async def _remove_folder(session: GatewaySession, params: dict) -> dict:
    async with session._run.lock:
        return session._bridge.remove_folder(params.get("path", ""))


async def _switch_session(session: GatewaySession, params: dict) -> dict:
    # new_session 不带 thread_id → 生成新的。切 thread 会改写 bridge._config，
    # 须与运行中的轮次互斥。
    # workspace（可选）：会话跨项目，切到某会话时把本 bridge 的项目绑到它所属项目
    # （仅本会话引擎/hooks，不动进程 cwd、不影响其它会话）。
    tid = params.get("thread_id") or generate_thread_id()
    workspace = params.get("workspace") or ""
    # desktop 每会话一条独立连接：切回本会话只是对同一条连接重发「同 thread」的 switch
    # （重绑 workspace）。此时本会话可能正挂着审批 / 在跑——绝不能收尾它，否则「切走再切回
    # 审批还在」不成立，且会误杀挂起审批、或在挂起轮仍持着 run.lock 时让下面的 async with
    # 死等。只有真正切到「不同 thread」才需先收尾当前轮（单 bridge 单 run.task 的限制）。
    if tid == session._bridge.current_thread_id and session.has_active_turn():
        return {"thread_id": tid}
    changing_thread = tid != session._bridge.current_thread_id
    if changing_thread:
        await session._finalize_active_turn(wait=True)
    async with session._run.lock:
        # 先切 thread 再绑项目：set_workspace 关的是 current_thread 的 shell，必须等
        # current_thread 已是切入的 tid，否则会关到切出会话的 shell、而切入会话的陈旧
        # shell 不被重置（见 review #9）。
        session._bridge.switch_thread(tid)
        # 绑定态不跟着 thread 走（switch_thread 不碰它）：本轮是否重新确认过绑定，
        # 统一在这三个分支后判定，换了新 thread 又没确认过就清绑定态——不留分支缝隙
        # 沿用上一个 thread 的绑定态（旧写法按「失败」「未带 workspace」各写一次
        # mark_workspace_unbound，漏了「workspace 已等于当前引擎目录，跳过 set_workspace」
        # 这个分支，此时绑定态本该确认为真却原样留着换 thread 前的旧值）。
        bound_this_call = False
        if workspace and workspace != session._bridge.workspace_dir:
            # 项目目录可能已被删/改名：绑定失败也要继续切会话，否则整个 RPC 报错、
            # 前端切会话卡死。降级为「不绑项目，仍打开会话」。
            try:
                await session._bridge.set_workspace(workspace)
                bound_this_call = True
            except (ValueError, OSError) as e:
                logger.warning(
                    "switch_session 绑定项目目录失败(%s)，仅切会话: %s", workspace, e
                )
        elif workspace:
            # 请求的 workspace 已经就是引擎当前指向的目录：等效于已确认绑定，
            # 不必再走一次 set_workspace
            session._bridge.mark_workspace_bound()
            bound_this_call = True
        if changing_thread and not bound_this_call:
            session._bridge.mark_workspace_unbound()
    return {"thread_id": tid}


async def _pin_session(session: GatewaySession, params: dict) -> dict:
    tid = params.get("thread_id", "")
    pinned = bool(params.get("pinned", False))
    update_meta(tid, pinned=pinned)
    return {"thread_id": tid, "pinned": pinned}


async def _rename_session(session: GatewaySession, params: dict) -> dict:
    tid = params.get("thread_id", "")
    title = params.get("title", "")
    update_meta(tid, title=title)
    return {"thread_id": tid, "title": title}


async def _delete_session(session: GatewaySession, params: dict) -> dict:
    tid = params.get("thread_id", "")
    async with session._run.lock:
        # 渠道会话（清空会话）：持渠道侧运行锁再删，避开在途轮把删掉的历史写回；
        # 非渠道 thread 不在任何池里，thread_lock 恒返回 None。
        # 轮可能跑数分钟，等 5s 仍占用则如实报错让用户稍后再试，不无限挂 RPC。
        chan_lock = manager.thread_lock(tid)
        if chan_lock is not None:
            try:
                await asyncio.wait_for(chan_lock.acquire(), timeout=5.0)
            except TimeoutError:
                raise ValueError("渠道会话正在执行，请稍后再试") from None
            try:
                await session._bridge.delete_thread(tid)
            finally:
                chan_lock.release()
        else:
            await session._bridge.delete_thread(tid)
    delete_meta(tid)
    # 渠道会话：广播给其他连接/旁观视图刷新（与渠道侧 /clear 同口径）
    if channel_name := _channel_of(tid):
        session._hub.on_channel_activity(tid, channel_name)
    return {"thread_id": tid}


async def _list_sessions_rpc(session: GatewaySession, params: dict) -> dict:
    return await _list_sessions(session._bridge, params)


async def _load_history_rpc(session: GatewaySession, params: dict) -> dict:
    return await _load_history(session._bridge, params)


async def _list_bg_tasks(session: GatewaySession, params: dict) -> dict:
    """全部后台任务快照（前端按当前 thread_id 过滤）。"""
    return {"tasks": serialize_bg_tasks()}


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


async def _stop_bg_task(session: GatewaySession, params: dict) -> dict:
    """停止运行中的后台任务（drawer 停止按钮）；仅限本会话任务。"""
    from lumi.agents.runtime.bg_process import cancel_background_task

    task_id = params.get("task_id", "")
    if not _owns_bg_task(session._bridge, task_id):
        return {"stopped": False, "error": "任务不属于当前会话"}
    return {"stopped": await cancel_background_task(task_id)}


async def _dismiss_bg_task(session: GatewaySession, params: dict) -> dict:
    """从列表移除一个终态后台任务（drawer 移除 ✕）；仅限本会话任务。"""
    from lumi.agents.runtime.bg_tasks import get_task_registry

    task_id = params.get("task_id", "")
    if not _owns_bg_task(session._bridge, task_id):
        return {"dismissed": False}
    return {"dismissed": get_task_registry().dismiss(task_id)}


async def _clear_finished_bg_tasks(session: GatewaySession, params: dict) -> dict:
    """清除当前会话的全部终态后台任务（drawer 头部「清除已完成」）。"""
    from lumi.agents.runtime.bg_tasks import get_task_registry

    return {
        "cleared": get_task_registry().clear_finished(session._bridge.current_thread_id)
    }


# 非流式 RPC 分发表。契约测试从 IMPLEMENTED_METHODS 读取实现的方法集合，
# 新增方法只需在此登记 + events.json 声明，漂移会被测试直接抓住。
_RPC_HANDLERS = {
    "stop": _stop,
    "resume": _resume,
    "list_commands": _list_commands,
    "list_providers": _list_providers,
    "test_provider": _test_provider,
    "set_provider": _set_provider,
    "save_provider": _save_provider,
    "delete_provider": _delete_provider,
    "set_effort": _set_effort,
    "set_classifier": _set_classifier,
    "set_titler": _set_titler,
    "set_tool_mode": _set_tool_mode,
    "set_workspace": _set_workspace,
    "list_projects": _list_projects,
    "add_project": _add_project,
    "remove_project": _remove_project,
    "rename_project": _rename_project,
    "set_default_project": _set_default_project,
    "list_dir": _list_dir,
    "make_dir": _make_dir,
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
IMPLEMENTED_METHODS = (
    frozenset(_RPC_HANDLERS)
    | _STREAMING_METHODS
    | CRON_METHODS
    | CHANNEL_METHODS
    | MCP_METHODS
)


class GatewaySession:
    """一条连接的会话编排：吸收所有运行调度 / RPC 分发 / 通知注入。

    持有独立 AgentBridge、传输 Channel、进程级广播 BroadcastHub；同一时刻只跑一轮
    用户流式响应（task），handle_frame 持续读帧使运行期间仍能收到 stop。
    """

    def __init__(
        self, bridge: AgentBridge, channel: Channel, hub: BroadcastHub
    ) -> None:
        self._bridge = bridge
        self._channel = channel
        self._hub = hub
        self._run = _RunState()
        self._rpc_tasks: set[asyncio.Task] = set()
        self._notif_task: asyncio.Task | None = None
        # 断连续接：detached（断开但仍挂着活跃轮）期间的 TTL 兜底回收 task
        self._ttl_task: asyncio.Task | None = None
        # 当前 _run.task 是否为后台通知合成轮（无用户在等）——断连时不值得 detach 续接
        self._synthetic_run: bool = False
        # 标题生成状态（按 thread）：pending 防同线程并发生成（后发的会抢在
        # 首条话题消息前定标题）；done 是本连接已知不必再试的 thread（已定稿 /
        # 手动名 / 生成失败放弃），使帧路径免于每条消息读盘
        self._title_pending: set[str] = set()
        self._title_done: set[str] = set()

    @property
    def current_thread_id(self) -> str:
        return self._bridge.current_thread_id

    def _ready_frame(self) -> dict:
        return event_frame(
            "gateway.ready",
            self._bridge.current_thread_id,
            {
                "model": self._bridge.model_name,
                "workspace": self._bridge.workspace_dir,
                # workspace 未绑定时仍会给出进程 cwd 兜底值（仅供展示），前端须据此区分
                # "真绑定的项目" 与 "兜底路径"——否则未绑定会话会把兜底 cwd 误当项目存进
                # workspaceDir，污染侧栏分组等展示。
                "workspace_bound": self._bridge.workspace_bound,
                # 续接时本会话可能仍挂着活跃 / 审批轮：带上运行态，让重连 / 重载后的前端恢复
                # running（否则 stop 隐藏、输入栏当空闲启用、续跑正文以非运行态渲染）。
                "running": self.has_active_turn(),
            },
        )

    async def start(self) -> None:
        """握手：发 gateway.ready、注册广播、拉起后台通知轮询。"""
        await self._channel.send(self._ready_frame())
        await self._attach_channel()
        # 后台任务完成通知轮询：与主接收循环并发，空闲时把队列通知注入新一轮推回前端
        self._notif_task = asyncio.create_task(self._notification_loop())

    async def _attach_channel(self) -> None:
        """把当前 channel 挂进广播通道（start / reattach 共用）。

        注册即声明其 MCP 池绑定（mcp_key 为 live 回调，随 set_workspace 跟随）；
        随后补发已完成加载的池状态——initialize 触发的后台加载可能在注册前就
        完成并广播（无人接收），detach 期间完成的广播同样落空，补发兜底这两个
        窗口，前端失败 toast 60s 去重天然消化重复。"""
        self._hub.register(self._channel, mcp_key=self._bridge.mcp_pool_key)
        payload = self._bridge.mcp_status_payload()
        if payload is not None:
            await self._channel.send(event_frame("mcp.status", "", payload))

    # ── 断连续接（Case 1）：会话生命周期与 WS 解耦 ──

    def has_active_turn(self) -> bool:
        """是否有活跃 / 挂起的轮（同 thread 切回守卫、流式互斥、断连续接判定的基础）。"""
        return self._run.task is not None and not self._run.task.done()

    def should_detach(self) -> bool:
        """WS 断开时是否值得续接：有活跃轮，且不是纯后台通知合成轮（无用户在等，除非它
        自身正挂着审批）。纯合成轮断连应正常 aclose，不占 registry / per-thread shell 满 8h。"""
        if not self.has_active_turn():
            return False
        return not self._synthetic_run or bool(self._bridge.pending_approval_events())

    def detach(self, registry: SessionRegistry) -> GatewaySession | None:
        """WS 断开但本会话仍有活跃轮：不 aclose，原地挂着等同 thread 重连续接。

        摘掉死 channel（换 Noop，断开期事件丢弃）、停掉通知轮、登记进 registry、挂 TTL
        兜底回收；run task 与 parked turn / broker / 挂起 Future 原样存活。返回被顶替的同
        thread 旧会话（罕见，调用方 aclose 它）。
        """
        self._hub.unregister(self._channel)
        self._channel = _NoopChannel()
        # 停掉通知轮：无 WS 期间没有可推送的对象，继续跑只会把本 thread 的通知 drain 进
        # NoopChannel 白白丢弃（reattach 时再起）。
        if self._notif_task is not None:
            self._notif_task.cancel()
            self._notif_task = None
        displaced = registry.add(self.current_thread_id, self)
        self._ttl_task = asyncio.create_task(self._ttl_expire(registry))
        return displaced

    async def _ttl_expire(self, registry: SessionRegistry) -> None:
        """detached 后无人接回到 TTL → 回收，防进程内泄漏。"""
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(_DETACH_TTL_SECONDS)
            self._ttl_task = None
            registry.discard(self.current_thread_id, self)
            await self.aclose()

    async def reattach(self, channel: Channel) -> None:
        """同 thread 的 WS 重连：接上新 channel 续接挂起的会话。

        取消 TTL、换 channel、重注册广播、重发 gateway.ready，并把挂起的审批/澄清卡片
        再推一遍（Future 仍在，用户应答即续跑）。notif/run task 与 parked turn 全程未停。
        """
        if self._ttl_task is not None:
            self._ttl_task.cancel()
            self._ttl_task = None
        self._channel = channel
        await self._attach_channel()
        # 重起通知轮（detach 时停掉了）：恢复后台任务完成反馈推送
        if self._notif_task is None:
            self._notif_task = asyncio.create_task(self._notification_loop())
        await channel.send(self._ready_frame())
        for evt in self._bridge.pending_approval_events():
            await channel.send(bridge_event_to_wire(evt, self.current_thread_id))

    async def handle_frame(self, frame: dict) -> None:
        """处理一帧 client → server 请求（流式 spawn 可取消 task，其余 spawn RPC task）。"""
        rid = frame.get("id")
        method = frame.get("method", "")
        params = frame.get("params") or {}

        # 流式方法 spawn 成独立 task：主循环立即回到读帧，使运行期间仍能收到 stop
        if method in _STREAMING_METHODS:
            # 渠道会话只读旁观（服务端兜底，非仅 UI）：desktop 与 IM 侧各持独立
            # bridge/锁，从这里发消息会绕过渠道的会话串行化、并发写坏同一 thread
            if _channel_of(self.current_thread_id):
                if rid is not None:
                    await self._channel.send(
                        {"id": rid, "error": {"message": "渠道会话为只读旁观"}}
                    )
                return
            if self.has_active_turn():
                if rid is not None:
                    await self._channel.send(
                        {"id": rid, "error": {"message": "已有任务在执行"}}
                    )
                return
            # 权威关卡（send_message + run_command 均覆盖，见上方 _STREAMING_METHODS）：
            # 未绑定项目（open 未带 workspace 且未调过 set_workspace）一律拒绝——否则会静默
            # 落在 workspace_dir 兜底的进程 cwd 上，工作区边界形同虚设。前端已引导先选项目，
            # 这里兜底防绕过（见 desktop App.tsx 的 requireProject/goNewChat）。
            if not self._bridge.workspace_bound:
                if rid is not None:
                    await self._channel.send(
                        {"id": rid, "error": {"message": "请先选择项目再开始对话"}}
                    )
                return
            self._run.task = asyncio.create_task(
                self._run_streaming_rpc(rid, self._stream_gen(method, params))
            )
            # 标题在消息发出时就机会性生成（不等本轮跑完，几秒内上屏，对齐
            # claude-code）；斜杠命令是合成消息、不是用户话题，不触发。
            if method == "send_message":
                self._maybe_generate_title(params.get("content", ""))
            return

        task = asyncio.create_task(self._run_rpc(rid, method, params))
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    async def aclose(self) -> None:
        """连接收尾：注销广播、取消 TTL/通知/RPC/流式 task、关闭 bridge。"""
        if self._ttl_task is not None:
            self._ttl_task.cancel()
            self._ttl_task = None
        self._hub.unregister(self._channel)
        if self._notif_task is not None:
            self._notif_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._notif_task
        for task in (*self._rpc_tasks, self._run.task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        await self._bridge.close()

    def _stream_gen(self, method: str, params: dict):
        """为流式方法构造对应的 BridgeEvent 异步生成器（不在此处启动迭代）。"""
        if method == "send_message":
            return self._bridge.stream_response(
                params.get("content", ""),
                tool_mode=params.get("tool_mode", "default"),
                execution_mode=params.get("execution_mode", "normal"),
                attachments=params.get("files"),
            )
        return self._bridge.stream_command(
            params.get("name", ""),
            extra_text=params.get("extra_text", ""),
            tool_mode=params.get("tool_mode", "default"),
        )

    async def _pump(self, gen) -> dict:
        """迭代 BridgeEvent 流逐条转 wire 推给客户端（假定已持有 run.lock）。

        在途审批不再令流以中断收尾——审批挂起期间该轮仍在 _pump 里活着、持着锁，应答经
        非流式 resume RPC 解开 Future 后继续吐事件，直到 turn.complete 自然结束。
        """
        async for evt in gen:
            await self._channel.send(
                bridge_event_to_wire(evt, self._bridge.current_thread_id)
            )
        return {"ok": True}

    async def _run_stream(self, gen) -> dict:
        """串行化地跑一轮事件流（用户消息 / 命令）。审批挂起期间持锁不放。"""
        async with self._run.lock:
            return await self._pump(gen)

    async def _finalize_active_turn(self, *, wait: bool) -> bool:
        """收尾当前活跃用户轮，返回是否确有一轮被收尾。

        优先把挂起审批以拒绝收尾，让本轮干净跑到 END、保留历史（同点"拒绝"）；无挂起审批
        （轮在流生成中途）才硬取消 task。wait=True 时等 task 结束——switch_session 取锁前
        须等，否则该轮正挂在审批上持着锁会死锁；stop 不等，以便快速回应。
        """
        rejected = self._bridge.reject_pending()
        task = self._run.task
        if task is None or task.done():
            return rejected > 0
        if rejected == 0:
            task.cancel()
        if wait:
            with suppress(asyncio.CancelledError, Exception):
                await task
        return True

    async def _finish_cancelled_turn(self) -> None:
        """被 stop 取消后的统一收尾：补发 turn.complete 结束前端 running 态。
        用户流式轮与后台通知轮共用。"""
        with suppress(Exception):
            await self._channel.send(
                event_frame(
                    str(EventKind.TURN_COMPLETE),
                    self._bridge.current_thread_id,
                    {},
                )
            )

    async def _run_streaming_rpc(self, rid, gen) -> None:
        """在独立 task 里跑一轮流式响应；被 stop 取消时给前端补发 turn.complete 收尾。

        放到 task 中（而非主循环内 await）是为了让主循环能在运行期间继续读帧、
        收到 stop 后取消本 task。
        """
        try:
            result = await self._run_stream(gen)
            if rid is not None:
                await self._channel.send({"id": rid, "result": result})
        except asyncio.CancelledError:
            # 被 stop 取消：本轮作废，通知前端结束 running 态（吞掉取消，已妥善收尾）
            await self._finish_cancelled_turn()
            if rid is not None:
                with suppress(Exception):
                    await self._channel.send({"id": rid, "result": {"stopped": True}})
        except Exception as e:
            logger.error("[WS] 流式任务失败: %s", e, exc_info=True)
            if rid is not None:
                with suppress(Exception):
                    await self._channel.send({"id": rid, "error": {"message": str(e)}})
        finally:
            self._run.task = None

    def _maybe_generate_title(self, content: str | list) -> None:
        """用户消息发出时机会性生成会话标题（后台跑，不等本轮完成）。

        第 1 条可见用户消息：直接从消息文本生成；已有 auto_title 则到第 3 条时
        用对话尾部再生成一次（纠正话题漂移）后定稿（auto_title_final）。手动
        title / 渠道会话不生成。帧路径只做内存判断，读盘与状态检查全在后台任务里。
        """
        tid = self._bridge.current_thread_id
        if (
            not tid
            or tid in self._title_done
            or tid in self._title_pending
            or _channel_of(tid)
        ):
            return
        text = extract_text_content(content).strip()
        if not text:
            return
        # pending 并发闩：首条消息的生成还在跑时，后续消息不再另起生成任务
        # （否则后发的会用跟进语单独定标题、抢掉真正带话题的首条）
        self._title_pending.add(tid)
        task = asyncio.create_task(self._generate_title(tid, text))
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    async def _generate_title(self, tid: str, text: str) -> None:
        from lumi.gateway.titler import generate_title, refresh_digest

        try:
            entry = load_all().get(tid, {})
            if (
                entry.get("title")
                or entry.get("channel_title")
                or entry.get("auto_title_final")
            ):
                self._title_done.add(tid)
                return
            refresh = bool(entry.get("auto_title"))
            digest = text
            if refresh:
                digest = refresh_digest(await self._bridge.snapshot_messages(tid), text)
                if not digest:  # 还没到第 3 条可见用户消息
                    return
            title = await generate_title(digest)
            if not title:
                return
            # 生成期间会话可能已被删除（delete_meta 已清）：无 checkpoint 即不写，
            # 否则会往 sidecar 复活一条永久幽灵条目
            if not await self._bridge.snapshot_messages(tid):
                return
            # 生成期间用户可能已手动重命名：写入前重查（到写盘无 await，无竞态窗），
            # 手动名永远优先
            entry = load_all().get(tid, {})
            if entry.get("title") or entry.get("channel_title"):
                self._title_done.add(tid)
                return
            update_meta(tid, auto_title=title, auto_title_final=refresh)
            if refresh:
                self._title_done.add(tid)
            self._hub.on_session_title(tid, title)
        except Exception:
            logger.warning("[titler] 生成会话标题失败 thread=%s", tid, exc_info=True)
            # 现实中的失败（titler 指针密钥失效等）是持久性的：本连接内直接放弃，
            # 不为每条消息白付一次注定失败的 LLM 调用
            self._title_done.add(tid)
        finally:
            self._title_pending.discard(tid)

    async def _notification_loop(self) -> None:
        """后台任务完成通知轮询。

        Agent 空闲时取出通知队列，作为不可见 meta 消息注入触发新一轮，让模型读取输出
        文件并把结果主动流式推回 desktop——否则通知只会堆积在队列里无人取用，桌面端
        永远收不到后台任务的完成反馈。
        """
        while True:
            await asyncio.sleep(NOTIFICATION_POLL_INTERVAL)
            # 无归属本 thread 的通知（绝大多数 tick）时不去抢 run.lock，避免在流式轮
            # 后面排队——渠道会话的通知会在队列里合法滞留（等渠道 poller 认领），
            # 全局非空不代表本会话有活干。审批挂起期间该轮持着 run.lock，下面
            # async with 自然被挡到审批结束，不会插入到挂起轮中间。
            if not self._bridge.has_notifications(self._bridge.current_thread_id):
                continue
            # 渠道会话旁观连接不消费通知：注入 meta 轮 = 绕过渠道会话锁并发写共享
            # thread（与 handle_frame 只读守卫同因）。通知留在队列，宁滞留不写坏。
            if _channel_of(self._bridge.current_thread_id):
                continue
            async with self._run.lock:
                # 只认领归属本连接当前 thread 的通知——队列是进程级共享的，
                # 按归属认领才不会把其他会话的后台任务通知抢到本会话注入
                hint = self._bridge.drain_notification_hint(
                    self._bridge.current_thread_id
                )
                if not hint:
                    continue
                logger.info("[WS] 注入后台任务通知")
                # 挂到 _run.task：否则 stop 取消不了这一轮，且新 send_message 会卡在
                # run.lock 上直到 meta 轮跑完（UI 挂死）。设为 task 后，stop 可取消、
                # 期间的新消息走 handle_frame 的 busy-check 得到「已有任务在执行」。
                # 标记 meta 轮：断连时它不值得 detach 续接（无用户在等，见 should_detach）
                self._synthetic_run = True
                self._run.task = asyncio.create_task(
                    self._pump(
                        self._bridge.stream_response(
                            hint, tool_mode="default", synthetic=True
                        )
                    )
                )
                try:
                    await self._run.task
                except asyncio.CancelledError:
                    # 被 stop 取消：作废本轮并补发 turn.complete 收尾，通知轮继续
                    await self._finish_cancelled_turn()
                except Exception:
                    # 连接断裂等：主循环会随之收尾并取消本任务，这里仅记录不致命
                    logger.error("[WS] 后台通知轮执行失败", exc_info=True)
                finally:
                    self._run.task = None
                    self._synthetic_run = False

    async def _dispatch(self, method: str, params: dict) -> dict:
        """执行一个非流式 RPC 方法并返回结果（在独立 task 中运行，见 _run_rpc）。

        流式方法（send_message / resume / run_command）不走这里，由 handle_frame 单独
        spawn 成可取消的 task（见 _run_streaming_rpc）。
        """
        handler = _RPC_HANDLERS.get(method)
        if handler is not None:
            return await handler(self, params)
        if method in CRON_METHODS:
            return await dispatch_cron(method, params)
        if method in CHANNEL_METHODS:
            return await dispatch_channel(method, params)
        if method in MCP_METHODS:
            return await dispatch_mcp(method, params)
        raise ValueError(f"未知方法: {method}")

    async def _run_rpc(self, rid, method: str, params: dict) -> None:
        """在独立 task 中执行非流式 RPC 并回发响应帧。

        需要 run.lock 的方法（delete_session / set_provider 等）在流式轮进行中
        会等锁——若 inline await 在接收循环里，等锁期间连 stop 帧都读不到。
        """
        try:
            result = await self._dispatch(method, params)
            if rid is not None:
                await self._channel.send({"id": rid, "result": result})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[WS] 处理 %s 失败: %s", method, e, exc_info=True)
            if rid is not None:
                with suppress(Exception):
                    await self._channel.send({"id": rid, "error": {"message": str(e)}})

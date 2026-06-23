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
import os
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from lumi.gateway.bridge import AgentBridge, EventKind
from lumi.gateway.broadcast import BroadcastHub, serialize_bg_tasks
from lumi.gateway.channel import Channel
from lumi.gateway.cron_rpc import CRON_METHODS, dispatch_cron
from lumi.gateway.projects import (
    add_project,
    list_projects,
    remove_project,
    rename_project,
    touch_project,
)
from lumi.gateway.protocol import bridge_event_to_wire, event_frame
from lumi.sessions.message_text import (
    extract_human_display_text,
    extract_text_content,
)
from lumi.sessions.message_visibility import should_show_human_message
from lumi.sessions.session_meta import delete_meta, load_all, update_meta
from lumi.sessions.session_store import list_sessions
from lumi.utils.constants import ATTACHED_FILE_TAG, NOTIFICATION_POLL_INTERVAL
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id

# 流以中断事件收尾 → 该轮尚未结束，正等待客户端 resume，期间不可插入后台通知轮
_INTERRUPT_KINDS = frozenset({EventKind.CLARIFY, EventKind.APPROVAL, EventKind.PLAN})

# 需后台 task 承载、可被 stop 取消的流式方法
_STREAMING_METHODS = frozenset({"send_message", "resume", "run_command"})


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


_ATTACHED_FILE_RE = re.compile(
    rf"<{ATTACHED_FILE_TAG}>(.*?)</{ATTACHED_FILE_TAG}>", re.DOTALL
)


def _extract_files(content) -> list[dict]:
    """从 HumanMessage 提取注入的文件附件，还原前端文件胶囊。

    发送侧把附件路径包在 <attached-file> 标签内（见 desktop send），
    此处正则取回，name 取 basename。
    """
    raw = extract_text_content(content)
    files: list[dict] = []
    for path in _ATTACHED_FILE_RE.findall(raw):
        p = path.strip()
        if p:
            files.append({"path": p, "name": Path(p).name})
    return files


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
            if not should_show_human_message(m):
                continue
            text = extract_human_display_text(m.content)
            images = _extract_images(m.content)
            files = _extract_files(m.content)
            if text or images or files:
                item = {"kind": "user", "text": text}
                if images:
                    item["images"] = images
                if files:
                    item["files"] = files
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
    """单条连接的运行协调状态。

    lock 串行化所有会改写 bridge 运行态的操作（用户轮 / 后台通知轮 / 切换会话），
    确保同一时刻 bridge 上只跑一件事。awaiting_resume 标记上一轮以中断收尾、正等待
    客户端 resume，此期间后台通知轮不得插入（否则会破坏挂起的中断状态）。
    task 持有当前正在跑的用户流式轮——独立于主接收循环，以便 stop 帧能取消它。
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    awaiting_resume: bool = False
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
                "title": entry.get("title", ""),
                "pinned": bool(entry.get("pinned", False)),
                "created_at": s.created_at.isoformat(),
                "message_count": s.message_count,
                "display_time": s.display_time,
                "workspace_dir": s.workspace_dir,
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


async def _stop(session: GatewaySession, params: dict) -> dict:
    # 中止当前用户轮：取消 task，其取消处理会补发 turn.complete 收尾
    task = session._run.task
    if task is not None and not task.done():
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}


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


# 远程目录浏览器：在「本后端」文件系统上浏览/建目录。前端按机器经各自控制连接调用，
# 故对远程机器即浏览远程文件系统（创建远程项目时选/建目录用）。
async def _list_dir(session: GatewaySession, params: dict) -> dict:
    raw = params.get("path") or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(raw))
    try:
        dirs = sorted(
            e.name
            for e in os.scandir(path)
            if e.is_dir() and not e.name.startswith(".")
        )
    except OSError:
        dirs = []
    parent = os.path.dirname(path)
    return {"path": path, "parent": None if parent == path else parent, "dirs": dirs}


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
    async with session._run.lock:
        # 先切 thread 再绑项目：set_workspace 关的是 current_thread 的 shell，必须等
        # current_thread 已是切入的 tid，否则会关到切出会话的 shell、而切入会话的陈旧
        # shell 不被重置（见 review #9）。
        session._bridge.switch_thread(tid)
        if workspace and workspace != session._bridge.workspace_dir:
            # 项目目录可能已被删/改名：绑定失败也要继续切会话，否则整个 RPC 报错、
            # 前端切会话卡死。降级为「不绑项目，仍打开会话」。
            try:
                await session._bridge.set_workspace(workspace)
            except (ValueError, OSError) as e:
                logger.warning(
                    "switch_session 绑定项目目录失败(%s)，仅切会话: %s", workspace, e
                )
        session._run.awaiting_resume = False
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
        await session._bridge.delete_thread(tid)
    delete_meta(tid)
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
IMPLEMENTED_METHODS = frozenset(_RPC_HANDLERS) | _STREAMING_METHODS | CRON_METHODS


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

    async def start(self) -> None:
        """握手：发 gateway.ready、注册广播、拉起后台通知轮询。"""
        await self._channel.send(
            event_frame(
                "gateway.ready",
                self._bridge.current_thread_id,
                {
                    "model": self._bridge.model_name,
                    "workspace": self._bridge.workspace_dir,
                },
            )
        )
        # 注册到 cron 结果广播通道：任务完成/运行状态变化实时推给本连接
        self._hub.register(self._channel)
        # 后台任务完成通知轮询：与主接收循环并发，空闲时把队列通知注入新一轮推回前端
        self._notif_task = asyncio.create_task(self._notification_loop())

    async def handle_frame(self, frame: dict) -> None:
        """处理一帧 client → server 请求（流式 spawn 可取消 task，其余 spawn RPC task）。"""
        rid = frame.get("id")
        method = frame.get("method", "")
        params = frame.get("params") or {}

        # 流式方法 spawn 成独立 task：主循环立即回到读帧，使运行期间仍能收到 stop
        if method in _STREAMING_METHODS:
            if self._run.task is not None and not self._run.task.done():
                if rid is not None:
                    await self._channel.send(
                        {"id": rid, "error": {"message": "已有任务在执行"}}
                    )
                return
            self._run.task = asyncio.create_task(
                self._run_streaming_rpc(rid, self._stream_gen(method, params))
            )
            return

        task = asyncio.create_task(self._run_rpc(rid, method, params))
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    async def aclose(self) -> None:
        """连接收尾：注销广播、取消通知/RPC/流式 task、关闭 bridge。"""
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
            )
        if method == "resume":
            return self._bridge.stream_resume(params.get("value"))
        return self._bridge.stream_command(
            params.get("name", ""),
            extra_text=params.get("extra_text", ""),
            tool_mode=params.get("tool_mode", "default"),
        )

    async def _pump(self, gen) -> dict:
        """迭代 BridgeEvent 流逐条转 wire 推给客户端（假定已持有 run.lock）。

        依据最后一个事件是否为中断更新 awaiting_resume：以中断收尾 → 等待 resume。
        """
        last_kind = None
        async for evt in gen:
            last_kind = evt.kind
            await self._channel.send(
                bridge_event_to_wire(evt, self._bridge.current_thread_id)
            )
        self._run.awaiting_resume = last_kind in _INTERRUPT_KINDS
        return {"ok": True}

    async def _run_stream(self, gen) -> dict:
        """串行化地跑一轮事件流（用户消息 / resume / 命令）。"""
        async with self._run.lock:
            return await self._pump(gen)

    async def _finish_cancelled_turn(self) -> None:
        """被 stop 取消后的统一收尾：清 awaiting_resume + 补发 turn.complete 结束前端
        running 态。用户流式轮与后台通知轮共用。"""
        self._run.awaiting_resume = False
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

    async def _notification_loop(self) -> None:
        """后台任务完成通知轮询。

        Agent 空闲时取出通知队列，作为不可见 meta 消息注入触发新一轮，让模型读取输出
        文件并把结果主动流式推回 desktop——否则通知只会堆积在队列里无人取用，桌面端
        永远收不到后台任务的完成反馈。
        """
        while True:
            await asyncio.sleep(NOTIFICATION_POLL_INTERVAL)
            # 队列空（绝大多数 tick）时不去抢 run.lock，避免在流式轮后面排队
            if self._run.awaiting_resume or not self._bridge.has_notifications():
                continue
            async with self._run.lock:
                # 抢到锁后复检：等锁期间用户轮可能刚以中断收尾
                if self._run.awaiting_resume:
                    continue
                # 只认领归属本连接当前 thread 的通知——队列是进程级共享的，
                # drain_all 会把其他会话的后台任务通知抢到本会话注入
                hint = self._bridge.drain_notification_hint(
                    self._bridge.current_thread_id
                )
                if not hint:
                    continue
                logger.info("[WS] 注入后台任务通知")
                # 挂到 _run.task：否则 stop 取消不了这一轮，且新 send_message 会卡在
                # run.lock 上直到 meta 轮跑完（UI 挂死）。设为 task 后，stop 可取消、
                # 期间的新消息走 handle_frame 的 busy-check 得到「已有任务在执行」。
                self._run.task = asyncio.create_task(
                    self._pump(
                        self._bridge.stream_response(
                            hint, tool_mode="default", is_meta=True
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

"""事件路由器 — 统一的事件分发器，解耦事件解析与 UI 编排。

职责：
- 状态机转换 (_transition)
- token 计数、计时、状态栏更新
- 子代理事件走 AgentGroup 轻量摘要模式
- 将主流程事件委托给 WidgetAssembler 进行 widget 组装
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from lumi.agents.bridge import BridgeEvent, EventKind
from lumi.tui.render_items import (
    AgentEndItem,
    AgentStartItem,
    ToolEndItem,
    ToolStartItem,
)
from lumi.tui.run_state import RunContext, RunPhase
from lumi.tui.subagent_tracker import SubagentTracker

if TYPE_CHECKING:
    from textual.widget import Widget

    from lumi.tui.widget_assembler import WidgetAssembler
    from lumi.tui.widgets.chat_log import ChatLog

from lumi.utils.logger import logger

# 等待用户交互的阶段（计时暂停）
_WAITING_PHASES: Final[frozenset[RunPhase]] = frozenset(
    {RunPhase.WAITING_ASK, RunPhase.WAITING_APPROVAL, RunPhase.WAITING_PLAN_APPROVAL}
)

# agent 工具因 cancel/reject 结束时的输出文本
_AGENT_CANCEL_OUTPUTS: Final[frozenset[str]] = frozenset(
    {"用户中断了工具调用请求", "用户拒绝了工具执行"}
)

# EventKind → RunPhase 的确定性映射（不依赖旧状态）
# MODEL_END 不在此映射中，保持当前阶段不变
_PHASE_MAP: Final[dict[EventKind, RunPhase]] = {
    EventKind.MODEL_START: RunPhase.THINKING,
    EventKind.STREAM_TOKEN: RunPhase.STREAMING,
    EventKind.TOOL_CALL_CHUNK: RunPhase.STREAMING,  # chunk 阶段模型仍在流式输出
    EventKind.TOOL_START: RunPhase.TOOL_RUNNING,
    EventKind.TOOL_END: RunPhase.TOOL_RUNNING,
    EventKind.ASK: RunPhase.WAITING_ASK,
    EventKind.TOOL_APPROVAL: RunPhase.WAITING_APPROVAL,
    EventKind.EXIT_PLAN_MODE: RunPhase.WAITING_PLAN_APPROVAL,
    EventKind.DONE: RunPhase.IDLE,
    EventKind.ERROR: RunPhase.IDLE,
}


class AppCallbacks(Protocol):
    """EventRouter 回调 app 的最小接口。"""

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None: ...
    async def _handle_tool_approval(
        self, evt: BridgeEvent, chat_log: ChatLog
    ) -> None: ...
    async def _handle_exit_plan_mode(
        self, evt: BridgeEvent, chat_log: ChatLog
    ) -> None: ...
    def _sync_plan_mode_from_tool(self) -> None: ...
    async def _show_error(self, chat_log: ChatLog, error: str) -> None: ...
    def _finish_run(self) -> None: ...
    def _query_safe(self, widget_type: type[Widget]) -> Widget | None: ...
    def _update_todos_bar(self, todos: list[dict]) -> None: ...


class EventRouter:
    """统一的事件分发器。

    状态机、token 计数等 live-only 逻辑在此处理，
    widget 创建和分组委托给 WidgetAssembler。
    """

    def __init__(
        self,
        run: RunContext,
        assembler: WidgetAssembler,
        tracker: SubagentTracker,
        app: AppCallbacks,
    ) -> None:
        self._run = run
        self._asm = assembler
        self._tracker = tracker
        self._app = app

    # ── 公开入口 ──

    async def dispatch(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """入口：解析上下文 → 状态转换 → 渲染分发。"""
        # 子代理事件走轻量统计路径（审批/ask 除外，需要 UI 交互）
        if evt.parent_run_id and evt.kind not in (
            EventKind.TOOL_APPROVAL,
            EventKind.ASK,
            EventKind.EXIT_PLAN_MODE,
        ):
            self._dispatch_subagent(evt)
            return

        # 主流程事件
        self._apply_main_flow_transition(evt)
        await self._route_main_event(evt, chat_log)

    # ── 子代理轻量路径 ──

    def _dispatch_subagent(self, evt: BridgeEvent) -> None:
        """子代理事件仅更新 AgentGroup 统计，不操作 DOM。"""
        group = self._asm.agent_group
        if group is None:
            logger.warning(
                "Subagent event discarded: agent_group is None "
                "(kind=%s, parent_run_id=%s)",
                evt.kind,
                evt.parent_run_id,
            )
            return
        run_id = evt.parent_run_id
        entry = group.get_entry(run_id)
        if entry is None:
            logger.debug(
                "_dispatch_subagent: parent_run_id=%s not in entries (kind=%s)",
                run_id,
                evt.kind,
            )
        match evt.kind:
            case EventKind.MODEL_START:
                group.record_model_start(run_id)
            case EventKind.STREAM_TOKEN:
                group.record_stream_token(run_id, evt.text)
            case EventKind.MODEL_END:
                group.record_tokens(run_id, evt.usage_metadata)
            case EventKind.TOOL_START:
                group.record_tool_start(run_id, evt.name, evt.args or {})
            case EventKind.TOOL_END:
                group.record_tool_end(run_id)
            case _:
                pass

    # ── 状态机 ──

    def _transition(self, evt: BridgeEvent) -> tuple[RunPhase, RunPhase]:
        """纯逻辑状态转换，不操作 DOM。返回 (old, new)。

        MODEL_END 和未知事件类型不改变当前阶段。
        """
        old = self._run.phase
        new = _PHASE_MAP.get(evt.kind, old)
        self._run.phase = new
        return old, new

    def _apply_main_flow_transition(self, evt: BridgeEvent) -> None:
        """主流程事件的状态机转换 + token 计数 + 状态栏更新。"""
        from lumi.tui.widgets.run_status_bar import RunStatusBar
        from lumi.tui.widgets.status_line import StatusLine

        old, new = self._transition(evt)

        # 离开 STREAMING → finalize assistant message
        if old == RunPhase.STREAMING and new != RunPhase.STREAMING:
            self._asm.finalize_assistant_msg()

        # token 跟踪
        if evt.kind == EventKind.STREAM_TOKEN:
            self._run.count_stream_token()
        self._run.accumulate_usage(evt.usage_metadata)

        # MODEL_END 携带精确 total_tokens，累加到会话计数并刷新状态行
        if evt.kind == EventKind.MODEL_END:
            self._run.commit_model_usage(evt.usage_metadata)
            sl = self._app._query_safe(StatusLine)
            if sl:
                sl.refresh_display()

        # 首次进入非 IDLE → 显示状态栏
        if old == RunPhase.IDLE and new != RunPhase.IDLE:
            bar = self._app._query_safe(RunStatusBar)
            if bar:
                bar.show_running()

        # 进入/离开等待用户交互阶段 → 暂停/恢复计时
        if new in _WAITING_PHASES and old not in _WAITING_PHASES:
            self._run.pause_timer()
        elif old in _WAITING_PHASES and new not in _WAITING_PHASES:
            self._run.resume_timer()

    # ── 主流程事件路由 ──

    async def _route_main_event(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程事件分发（子代理事件已在 dispatch 中拦截）。

        渲染类事件（STREAM_TOKEN、TOOL_START、TOOL_END、MODEL_END）失败时
        仅记录日志，不中断事件流；生命周期事件（DONE、ERROR）始终执行。
        """
        try:
            match evt.kind:
                case EventKind.STREAM_TOKEN:
                    await self._asm.append_stream_token(evt.text)
                case EventKind.TOOL_START:
                    await self._handle_tool_start(evt)
                case EventKind.TOOL_END:
                    await self._handle_tool_end(evt)
                case EventKind.MODEL_END:
                    self._asm.finalize_assistant_msg()
                case EventKind.ASK:
                    await self._asm.flush_groups()
                    await self._app._handle_ask(evt, chat_log)
                case EventKind.TOOL_APPROVAL:
                    await self._asm.flush_groups()
                    await self._app._handle_tool_approval(evt, chat_log)
                case EventKind.EXIT_PLAN_MODE:
                    await self._asm.flush_groups()
                    await self._app._handle_exit_plan_mode(evt, chat_log)
                case EventKind.DONE:
                    await self._asm.flush_groups()
                    if evt.usage_metadata and not self._run.cache_read_tokens:
                        self._run.commit_model_usage(evt.usage_metadata)
                    self._app._finish_run()
                case EventKind.ERROR:
                    await self._asm.flush_groups()
                    await self._app._show_error(chat_log, evt.error)
        except Exception:
            logger.error(
                "Event routing failed (kind=%s, name=%s)",
                evt.kind,
                evt.name,
                exc_info=True,
            )
            # 生命周期事件失败时仍须确保 run 正常结束
            if evt.kind in (EventKind.DONE, EventKind.ERROR):
                try:
                    self._app._finish_run()
                except Exception:
                    logger.error("_finish_run fallback also failed", exc_info=True)

    # ── 主流程 handlers ──

    async def _handle_tool_start(self, evt: BridgeEvent) -> None:
        """主流程 TOOL_START：委托给 WidgetAssembler，处理 SubagentTracker 注册。"""
        if evt.name == "agent" and evt.run_id:
            await self._handle_agent_start(evt)
            return

        # todos 工具 → 仅更新 #todos-bar，不创建 ToolBlock
        if evt.name == "todos" and evt.args:
            self._app._update_todos_bar(evt.args.get("todos", []))
            return

        # 非 agent 工具 → 委托给 assembler
        key = evt.tool_call_id or evt.name
        if key not in self._asm.tool_blocks:
            await self._asm.apply_item(
                ToolStartItem(
                    key=key,
                    name=evt.name,
                    args=evt.args or {},
                    approval_mode=False,
                )
            )

    async def _handle_agent_start(self, evt: BridgeEvent) -> None:
        """处理 agent 工具 TOOL_START（replay 复用或全新注册）。"""
        # replay 场景：审批后 resume 会重新发出 agent TOOL_START（新 run_id），
        # 尝试复用已有的 unmapped block，避免在 AgentGroup 中重复新增条目
        existing = self._tracker.find_unmapped_running(evt.args)
        if existing is not None:
            old_state = self._tracker.get_by_block(existing)
            old_run_id = old_state.run_id if old_state else ""
            self._tracker.remap(evt.run_id, existing)
            self._asm.tool_blocks[evt.run_id] = existing
            # 同步更新 AgentGroup 中的 run_id 映射
            if old_run_id and self._asm.agent_group:
                self._asm.agent_group.remap_agent(old_run_id, evt.run_id)
            return

        # 全新 agent 工具 → AgentGroup 轻量模式
        args = evt.args or {}
        await self._asm.apply_item(
            AgentStartItem(
                run_id=evt.run_id,
                agent_name=args.get("name", "agent"),
                prompt=args.get("prompt", ""),
            )
        )
        # 在 tracker 中注册占位 ToolBlock（审批事件需要通过 tracker 找到 parent）
        from lumi.tui.widgets.tool_block import ToolBlock

        placeholder = ToolBlock(evt.name, args)
        self._asm.tool_blocks[evt.run_id] = placeholder
        self._tracker.register(evt.run_id, placeholder)

    async def _handle_tool_end(self, evt: BridgeEvent) -> None:
        """主流程 TOOL_END：agent 工具有特殊逻辑，其他委托给 assembler。"""
        if evt.name == "agent" and evt.run_id:
            await self._handle_agent_end(evt)
            return

        # todos 工具 → 无 ToolBlock，跳过
        if evt.name == "todos":
            return

        # EnterPlanMode 工具结束 → 同步 InputBar 指示器
        if evt.name == "EnterPlanMode":
            self._app._sync_plan_mode_from_tool()

        # 非 agent 工具 → 委托给 assembler
        key = evt.tool_call_id or evt.name
        await self._asm.apply_item(
            ToolEndItem(
                key=key,
                name=evt.name,
                output=evt.output,
                is_error=False,
            )
        )

    async def _handle_agent_end(self, evt: BridgeEvent) -> None:
        """处理 agent 工具 TOOL_END（replay 重试、cancel 检测、正常结束）。"""
        key = evt.run_id
        block = self._asm.pop_tool_block(key)

        # replay 的空 output → 放回等待真正结束
        if block and not evt.output:
            replay_count = getattr(block, "_empty_end_count", 0) + 1
            block._empty_end_count = replay_count  # type: ignore[attr-defined]
            if replay_count > 1:
                logger.warning(
                    "Agent TOOL_END with empty output repeated %d times "
                    "(run_id=%s), possible bridge bug",
                    replay_count,
                    key,
                )
            self._asm.tool_blocks[key] = block
            return

        # cancel/reject → 标记错误
        if block and evt.output in _AGENT_CANCEL_OUTPUTS:
            if self._asm.agent_group:
                self._asm.agent_group.finish_agent_error(key, evt.output)
            self._tracker.mark_unmapped(evt.run_id)
            return

        # 正常结束
        self._tracker.unregister(evt.run_id)
        await self._asm.apply_item(
            AgentEndItem(run_id=key, output=evt.output, is_error=False)
        )

"""事件路由器 — 统一的事件分发器，解耦事件解析与 UI 编排。

从 app.py 抽取事件处理逻辑，包括：
- 状态机转换 (_transition)
- 渲染上下文解析 (_resolve_context)
- 共享渲染方法 (_render_stream_token / _render_tool_start / _render_tool_end)
- 主流程 handler (_handle_stream_token / _handle_tool_start / _handle_tool_end)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from lumi.tui.agent_bridge import BridgeEvent, EventKind
from lumi.tui.run_state import RenderState, RunContext, RunPhase
from lumi.tui.subagent_tracker import SubagentTracker

if TYPE_CHECKING:
    from textual.widget import Widget

    from lumi.tui.widgets.chat_log import ChatLog

logger = logging.getLogger(__name__)

# 等待用户交互的阶段（计时暂停）
_WAITING_PHASES: Final = frozenset({RunPhase.WAITING_ASK, RunPhase.WAITING_APPROVAL})

# agent 工具因 cancel/reject 结束时的输出文本，匹配后 block 保持 RUNNING 以便复用
_AGENT_CANCEL_OUTPUTS: Final = frozenset(
    {"用户中断了工具调用请求", "用户拒绝了工具执行"}
)


class AppCallbacks(Protocol):
    """EventRouter 回调 app 的最小接口。"""

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None: ...
    async def _handle_tool_approval(
        self, evt: BridgeEvent, chat_log: ChatLog
    ) -> None: ...
    async def _show_error(self, chat_log: ChatLog, error: str) -> None: ...
    def _finish_run(self) -> None: ...
    def _query_safe(self, widget_type: type[Widget]) -> Widget | None: ...


@dataclass(frozen=True)
class RenderContext:
    """事件渲染上下文 — 统一 mount_target + state + is_subagent 三元组。"""

    mount_target: Widget
    """挂载目标：ChatLog（主流程）或 ToolBlock.subagent_log（子代理）"""

    state: RenderState
    """渲染状态：RunContext（主流程）或 SubagentState（子代理）"""

    is_subagent: bool
    """是否为子代理事件"""


class EventRouter:
    """统一的事件分发器，解耦事件解析与 UI 编排。"""

    def __init__(
        self,
        run: RunContext,
        tracker: SubagentTracker,
        app: AppCallbacks,
    ) -> None:
        self._run = run
        self._tracker = tracker
        self._app = app

    # ── 公开入口 ──

    async def dispatch(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """入口：解析上下文 → 状态转换 → 渲染分发。"""
        # 1) 解析渲染上下文
        ctx = self._resolve_context(evt, chat_log)
        if ctx is None:
            return  # 子代理事件被丢弃

        # 2) 主流程：状态机转换 + token 计数（子代理跳过）
        if not ctx.is_subagent:
            self._apply_main_flow_transition(evt)

        # 3) 子代理：pending_dom_clear 检查
        if ctx.is_subagent:
            sa_state = self._tracker.get(evt.parent_run_id)
            if sa_state and sa_state.pending_dom_clear:
                if evt.kind in (EventKind.STREAM_TOKEN, EventKind.TOOL_START):
                    await ctx.mount_target.remove_children()
                    sa_state.pending_dom_clear = False

        # 4) 统一 match 分支
        await self._route_event(evt, chat_log, ctx)

    # ── 上下文解析 ──

    def _resolve_context(
        self, evt: BridgeEvent, chat_log: ChatLog
    ) -> RenderContext | None:
        """判定事件归属（主流程 or subagent），返回统一的渲染上下文。

        子代理事件无法路由时返回 None（事件被丢弃）。
        """
        if evt.parent_run_id and evt.kind not in (
            EventKind.TOOL_APPROVAL,
            EventKind.ASK,
        ):
            sa_state = self._tracker.get(evt.parent_run_id)
            if sa_state is None:
                logger.debug(
                    "[EventRouter] subagent event DROPPED: kind=%s name=%s "
                    "parent_run_id=%s (tracker miss)",
                    evt.kind,
                    evt.name,
                    evt.parent_run_id,
                )
                return None
            log = sa_state.agent_block.subagent_log
            if log is None:
                logger.debug(
                    "[EventRouter] subagent event DROPPED: kind=%s name=%s "
                    "parent_run_id=%s (subagent_log is None)",
                    evt.kind,
                    evt.name,
                    evt.parent_run_id,
                )
                return None
            logger.debug(
                "[EventRouter] subagent routed: kind=%s name=%s "
                "parent_run_id=%s tool_call_id=%s",
                evt.kind,
                evt.name,
                evt.parent_run_id,
                evt.tool_call_id,
            )
            return RenderContext(mount_target=log, state=sa_state, is_subagent=True)

        return RenderContext(mount_target=chat_log, state=self._run, is_subagent=False)

    # ── 状态机 ──

    def _transition(self, evt: BridgeEvent) -> tuple[RunPhase, RunPhase]:
        """纯逻辑状态转换，不操作 DOM。返回 (old, new)。"""
        old = self._run.phase
        match evt.kind:
            case EventKind.MODEL_START:
                new = RunPhase.THINKING
            case EventKind.STREAM_TOKEN:
                new = RunPhase.STREAMING
            case EventKind.MODEL_END:
                new = old  # 不改变可见状态
            case EventKind.TOOL_CALL_CHUNK:
                new = RunPhase.TOOL_CALL_PENDING
            case EventKind.TOOL_START:
                new = RunPhase.TOOL_RUNNING
            case EventKind.TOOL_END:
                new = RunPhase.TOOL_RUNNING
            case EventKind.ASK:
                new = RunPhase.WAITING_ASK
            case EventKind.TOOL_APPROVAL:
                new = RunPhase.WAITING_APPROVAL
            case EventKind.DONE | EventKind.ERROR:
                new = RunPhase.IDLE
            case _:
                new = old
        self._run.phase = new
        return old, new

    def _apply_main_flow_transition(self, evt: BridgeEvent) -> None:
        """主流程事件的状态机转换 + token 计数 + 状态栏更新。"""
        from lumi.tui.widgets.run_status_bar import RunStatusBar
        from lumi.tui.widgets.status_line import StatusLine

        old, new = self._transition(evt)

        # 离开 STREAMING → finalize assistant message
        if old == RunPhase.STREAMING and new != RunPhase.STREAMING:
            self._run.finalize_assistant_msg()

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

    # ── 事件路由 ──

    async def _route_event(
        self, evt: BridgeEvent, chat_log: ChatLog, ctx: RenderContext
    ) -> None:
        """按 EventKind 分发到对应 handler 或 AppCallbacks。"""
        match evt.kind:
            case EventKind.STREAM_TOKEN:
                if ctx.is_subagent:
                    await self._render_stream_token(evt, ctx.mount_target, ctx.state)
                else:
                    await self._handle_stream_token(evt, chat_log)
            case EventKind.TOOL_START:
                if ctx.is_subagent:
                    await self._render_tool_start(evt, ctx.mount_target, ctx.state)
                else:
                    await self._handle_tool_start(evt, chat_log)
            case EventKind.TOOL_END:
                if ctx.is_subagent:
                    await self._render_tool_end(evt, ctx.state)
                else:
                    await self._handle_tool_end(evt, chat_log)
            case EventKind.MODEL_END:
                ctx.state.finalize_assistant_msg()
            case EventKind.ASK:
                await self._app._handle_ask(evt, chat_log)
            case EventKind.TOOL_APPROVAL:
                await self._app._handle_tool_approval(evt, chat_log)
            case EventKind.DONE:
                # 从 state 补充 usage（仅当 MODEL_END 未提供 cache 详情时）
                if evt.usage_metadata and not self._run.cache_read_tokens:
                    self._run.commit_model_usage(evt.usage_metadata)
                self._app._finish_run()
            case EventKind.ERROR:
                await self._app._show_error(chat_log, evt.error)

    # ── 共享渲染方法（主流程和子代理复用）──

    async def _render_stream_token(
        self, evt: BridgeEvent, mount_target: Widget, state: RenderState
    ) -> None:
        """创建或追加 AssistantMessage（主流程与子代理共用）。"""
        from lumi.tui.widgets.assistant_message import AssistantMessage

        if state.assistant_msg is None:
            state.assistant_msg = AssistantMessage()
            logger.debug(
                "[EventRouter] STREAM_TOKEN: new AssistantMessage mounted in %s",
                type(mount_target).__name__,
            )
            await mount_target.mount(state.assistant_msg)
        state.assistant_msg.append_token(evt.text)

    async def _render_tool_start(
        self, evt: BridgeEvent, mount_target: Widget, state: RenderState
    ) -> None:
        """创建 ToolBlock 并挂载（主流程与子代理共用）。"""
        from lumi.tui.widgets.tool_block import ToolBlock

        state.finalize_assistant_msg()
        key = evt.tool_call_id or evt.name
        if key not in state.tool_blocks:
            block = ToolBlock(evt.name, evt.args or {})
            state.tool_blocks[key] = block
            logger.debug(
                "[EventRouter] TOOL_START: mounted ToolBlock(%s) key=%s in %s, "
                "children_count=%d",
                evt.name,
                key,
                type(mount_target).__name__,
                len(mount_target.children),
            )
            await mount_target.mount(block)
        else:
            logger.debug(
                "[EventRouter] TOOL_START: key=%s already in tool_blocks, skip mount",
                key,
            )

    async def _render_tool_end(self, evt: BridgeEvent, state: RenderState) -> None:
        """结束 ToolBlock（主流程与子代理共用）。"""
        state.finalize_assistant_msg()
        key = evt.tool_call_id or evt.name
        block = state.tool_blocks.pop(key, None)
        if block is None:
            for k, b in list(state.tool_blocks.items()):
                if b._name == evt.name:
                    block = state.tool_blocks.pop(k)
                    break
        if block:
            block.set_done(evt.output)
            logger.debug(
                "[EventRouter] TOOL_END: set_done ToolBlock(%s) key=%s",
                evt.name,
                key,
            )
        else:
            logger.warning(
                "[EventRouter] TOOL_END: ToolBlock NOT FOUND for name=%s key=%s "
                "(tool_blocks keys: %s)",
                evt.name,
                key,
                list(state.tool_blocks.keys()),
            )

    # ── 主流程 handlers ──

    async def _handle_stream_token(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        await self._render_stream_token(evt, chat_log, self._run)

    async def _handle_tool_start(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程 TOOL_START 处理，含 agent replay 逻辑。"""
        from lumi.tui.widgets.tool_block import ToolBlock

        # agent 工具用 run_id 作为 key（支持并发），其他工具用 tool_call_id 或 name
        key = (
            evt.run_id
            if evt.name == "agent" and evt.run_id
            else (evt.tool_call_id or evt.name)
        )
        # 审批模式下 ToolBlock 已在 TOOL_APPROVAL 阶段创建
        if key not in self._run.tool_blocks:
            # agent 工具：恢复场景下可能已有 block（replay 产生新 run_id），
            # 从 tracker 中查找未映射的 RUNNING agent block 并重新关联
            if evt.name == "agent" and evt.run_id:
                existing = self._tracker.find_unmapped_running(evt.args)
                if existing:
                    self._tracker.remap(evt.run_id, existing)
                    # DOM 清理推迟到子代理首个 STREAM_TOKEN/TOOL_START，
                    # 避免 agent 被立即 cancel 时丢失上一周期的可视记录。
                    self._run.tool_blocks[key] = existing
                    return
            block = ToolBlock(evt.name, evt.args or {}, approval_mode=evt.approval_mode)
            self._run.tool_blocks[key] = block
            if evt.name == "agent" and evt.run_id:
                self._tracker.register(evt.run_id, block)
            await chat_log.mount(block)
        else:
            # 已存在的 block，确保 tracker 映射更新（replay 场景）
            if evt.name == "agent" and evt.run_id:
                existing_block = self._run.tool_blocks[key]
                if self._tracker.get(evt.run_id) is None:
                    self._tracker.remap(evt.run_id, existing_block)

    async def _handle_tool_end(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程 TOOL_END 处理，含 agent cancel/replay 逻辑。"""
        # agent 工具用 run_id 作为 key（与 _handle_tool_start 一致）
        key = (
            evt.run_id
            if evt.name == "agent" and evt.run_id
            else (evt.tool_call_id or evt.name)
        )
        block = self._run.tool_blocks.pop(key, None)
        # Fallback: tool_call_id 可能在 TOOL_START/END 间不一致
        if block is None:
            for k, b in list(self._run.tool_blocks.items()):
                if b._name == evt.name:
                    block = self._run.tool_blocks.pop(k)
                    break
        if block:
            # agent 工具：replay 的 on_tool_end 没有 output，跳过 set_done
            # 保持 block 在 RUNNING 状态，等待真正的结束事件
            if block._is_agent and not evt.output:
                self._run.tool_blocks[key] = block  # 放回 tool_blocks
                return
            # agent 工具：cancel/reject 导致的结束，重置 block 以便 replay 复用
            if block._is_agent and evt.output in _AGENT_CANCEL_OUTPUTS:
                block.reset_for_retry()
                self._run.tool_blocks[key] = block  # 放回 tool_blocks
                if evt.run_id:
                    # 标记为 unmapped 而非 unregister，使 find_unmapped_running 能找到
                    self._tracker.mark_unmapped(evt.run_id)
                return
            # agent 工具结束前，finalize 子代理残留的 AssistantMessage
            if block._is_agent and evt.run_id:
                sa_state = self._tracker.unregister(evt.run_id)
                if sa_state:
                    sa_state.finalize_assistant_msg()
            block.set_done(evt.output)

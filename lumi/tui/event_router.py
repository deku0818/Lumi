"""事件路由器 — 统一的事件分发器，解耦事件解析与 UI 编排。

从 app.py 抽取事件处理逻辑，包括：
- 状态机转换 (_transition)
- 主流程 handler (_handle_stream_token / _handle_tool_start / _handle_tool_end)
- AgentGroup 轻量摘要模式（子代理事件仅更新统计，不渲染 DOM）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Protocol

from lumi.tui.agent_bridge import BridgeEvent, EventKind
from lumi.tui.run_state import RunContext, RunPhase
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


class EventRouter:
    """统一的事件分发器，解耦事件解析与 UI 编排。

    子代理事件走 AgentGroup 轻量摘要模式：仅更新统计数据，不渲染 DOM。
    """

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
        # 子代理事件走轻量统计路径（审批/ask 除外，需要 UI 交互）
        if evt.parent_run_id and evt.kind not in (
            EventKind.TOOL_APPROVAL,
            EventKind.ASK,
        ):
            self._dispatch_subagent(evt)
            return

        # 主流程事件
        self._apply_main_flow_transition(evt)
        await self._route_main_event(evt, chat_log)

    # ── 子代理轻量路径 ──

    def _dispatch_subagent(self, evt: BridgeEvent) -> None:
        """子代理事件仅更新 AgentGroup 统计，不操作 DOM。"""
        group = self._run.agent_group
        if group is None:
            logger.warning(
                "Subagent event discarded: agent_group is None "
                "(kind=%s, parent_run_id=%s)",
                evt.kind,
                evt.parent_run_id,
            )
            return
        run_id = evt.parent_run_id
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

    # ── 主流程事件路由 ──

    async def _route_main_event(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程事件分发（子代理事件已在 dispatch 中拦截）。"""
        match evt.kind:
            case EventKind.STREAM_TOKEN:
                await self._handle_stream_token(evt, chat_log)
            case EventKind.TOOL_START:
                await self._handle_tool_start(evt, chat_log)
            case EventKind.TOOL_END:
                await self._handle_tool_end(evt, chat_log)
            case EventKind.MODEL_END:
                self._run.finalize_assistant_msg()
            case EventKind.ASK:
                await self._finalize_active_group(chat_log)
                await self._app._handle_ask(evt, chat_log)
            case EventKind.TOOL_APPROVAL:
                await self._finalize_active_group(chat_log)
                await self._app._handle_tool_approval(evt, chat_log)
            case EventKind.DONE:
                await self._finalize_active_group(chat_log)
                if evt.usage_metadata and not self._run.cache_read_tokens:
                    self._run.commit_model_usage(evt.usage_metadata)
                self._app._finish_run()
            case EventKind.ERROR:
                await self._finalize_active_group(chat_log)
                await self._app._show_error(chat_log, evt.error)

    # ── ToolGroup 管理 ──

    async def _finalize_active_group(self, chat_log: "ChatLog | None" = None) -> None:
        """关闭当前活跃的 ToolGroup（遇到文本/交互事件时调用）。

        若只有一个待合并的 block（尚未创建 ToolGroup），直接挂载到 chat_log。
        """
        # 先处理待合并的单个 block
        if self._run.pending_block is not None and chat_log is not None:
            await chat_log.mount(self._run.pending_block)
            self._run.pending_block = None

        group = self._run.active_group
        if group is None:
            return
        self._run.active_group = None
        group.finalize_group()

    # ── 主流程 handlers ──

    async def _handle_stream_token(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程 STREAM_TOKEN 处理。"""
        from lumi.tui.widgets.assistant_message import AssistantMessage

        await self._finalize_active_group(chat_log)
        if self._run.assistant_msg is None:
            self._run.assistant_msg = AssistantMessage()
            await chat_log.mount(self._run.assistant_msg)
        self._run.assistant_msg.append_token(evt.text)

    async def _handle_tool_start(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程 TOOL_START 处理。

        agent 工具走 AgentGroup 轻量摘要模式，其他工具走 ToolGroup 合并。
        """
        from lumi.tui.widgets.agent_group import AgentGroup as AgentGroupCls
        from lumi.tui.widgets.tool_block import ToolBlock
        from lumi.tui.widgets.tool_group import ToolGroup, should_exclude_from_group

        # agent 工具 → AgentGroup 轻量模式
        if evt.name == "agent" and evt.run_id:
            agent_name = (evt.args or {}).get("name", "agent")
            prompt = (evt.args or {}).get("prompt", "")

            # 创建或复用 AgentGroup
            if self._run.agent_group is None:
                await self._finalize_active_group(chat_log)
                self._run.finalize_assistant_msg()
                group = AgentGroupCls()
                self._run.agent_group = group
                await chat_log.mount(group)

            self._run.agent_group.add_agent(evt.run_id, agent_name, prompt)
            # 在 tracker 中注册（审批事件需要通过 tracker 找到 parent）
            # 使用一个轻量占位 ToolBlock 仅用于 tracker 映射
            placeholder = ToolBlock(evt.name, evt.args or {})
            self._run.tool_blocks[evt.run_id] = placeholder
            self._tracker.register(evt.run_id, placeholder)
            return

        # 非 agent 工具的常规处理
        key = evt.tool_call_id or evt.name
        if key not in self._run.tool_blocks:
            block = ToolBlock(evt.name, evt.args or {}, approval_mode=evt.approval_mode)
            self._run.tool_blocks[key] = block

            if should_exclude_from_group(evt.name, evt.approval_mode):
                await self._finalize_active_group(chat_log)
                await chat_log.mount(block)
            elif (
                self._run.pending_block is not None
                or self._run.active_group is not None
            ):
                # 第二个及后续 block → 创建或追加到 ToolGroup
                if self._run.active_group is None:
                    group = ToolGroup()
                    self._run.active_group = group
                    await chat_log.mount(group)
                    # 把之前暂存的第一个 block 也加入 group
                    pending = self._run.pending_block
                    self._run.pending_block = None
                    if pending is not None:
                        await group.add_block(pending, pending._name, pending._args)
                await self._run.active_group.add_block(block, evt.name, evt.args or {})
            else:
                # 第一个 block → 暂存，等看后续是否有更多
                self._run.pending_block = block

    async def _handle_tool_end(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """主流程 TOOL_END 处理。

        agent 工具结束时通知 AgentGroup，其他工具走常规 ToolBlock 流程。
        """
        # agent 工具 → 通知 AgentGroup
        if evt.name == "agent" and evt.run_id:
            key = evt.run_id
            block = self._run.tool_blocks.pop(key, None)

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
                self._run.tool_blocks[key] = block
                return

            # cancel/reject → 通知 AgentGroup 标记错误
            if block and evt.output in _AGENT_CANCEL_OUTPUTS:
                if self._run.agent_group:
                    self._run.agent_group.finish_agent_error(key, evt.output)
                self._tracker.mark_unmapped(evt.run_id)
                return

            # 正常结束
            self._tracker.unregister(evt.run_id)
            if self._run.agent_group:
                self._run.agent_group.finish_agent(key, evt.output)
            return

        # 非 agent 工具的常规处理
        key = evt.tool_call_id or evt.name
        block = self._run.tool_blocks.pop(key, None)
        if block is None:
            for k, b in list(self._run.tool_blocks.items()):
                if b._name == evt.name:
                    block = self._run.tool_blocks.pop(k)
                    break
        if block is None:
            logger.warning(
                "TOOL_END dropped: no matching block (key=%s, name=%s, tracked=%s)",
                key,
                evt.name,
                list(self._run.tool_blocks.keys()),
            )
            return
        block.set_done(evt.output)
        if self._run.active_group is not None:
            self._run.active_group.notify_block_done(block)

"""Subagent 状态追踪器 — subagent 生命周期的唯一数据源

替代此前分散在 RunContext.agent_run_blocks、ToolBlock._subagent_*、
RunContext.last_approval_agent_block 中的状态管理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lumi.tui.widgets.assistant_message import AssistantMessage
    from lumi.tui.widgets.tool_block import ToolBlock

logger = logging.getLogger(__name__)


@dataclass
class SubagentState:
    """单个 subagent 的完整运行状态。"""

    run_id: str
    agent_block: "ToolBlock"
    tool_blocks: dict[str, "ToolBlock"] = field(default_factory=dict)
    assistant_msg: "AssistantMessage | None" = None
    pending_dom_clear: bool = False
    """remap 后设为 True，下一次挂载新子节点前清空旧 DOM children。"""

    def finalize_assistant_msg(self) -> None:
        """结束当前流式 AssistantMessage。"""
        if self.assistant_msg is not None:
            self.assistant_msg.finalize()
            self.assistant_msg = None


class SubagentTracker:
    """Subagent 状态的唯一数据源。

    所有 subagent 相关的查找、注册、注销都通过此类完成，
    避免在 ToolBlock/RunContext 中维护冗余映射。
    """

    def __init__(self) -> None:
        self._by_run_id: dict[str, SubagentState] = {}
        self._unmapped: list[SubagentState] = []
        self._approval_run_id: str | None = None

    # ── 注册 / 查找 / 注销 ──

    def register(self, run_id: str, agent_block: "ToolBlock") -> SubagentState:
        """注册新的 agent 工具运行。在 TOOL_START(name='agent') 时调用。"""
        state = SubagentState(run_id=run_id, agent_block=agent_block)
        self._by_run_id[run_id] = state
        return state

    def get(self, run_id: str) -> SubagentState | None:
        """通过 run_id 精确查找。优先查 _by_run_id，回退到 _unmapped。"""
        state = self._by_run_id.get(run_id)
        if state is not None:
            return state
        # replay fallback: _unmapped 中可能保留旧 run_id
        for s in self._unmapped:
            if s.run_id == run_id:
                return s
        return None

    def get_by_block(self, agent_block: "ToolBlock") -> SubagentState | None:
        """通过 ToolBlock 实例反查 SubagentState。"""
        for state in self._by_run_id.values():
            if state.agent_block is agent_block:
                return state
        for state in self._unmapped:
            if state.agent_block is agent_block:
                return state
        return None

    def unregister(self, run_id: str) -> SubagentState | None:
        """注销已完成的 agent 运行。"""
        return self._by_run_id.pop(run_id, None)

    def mark_unmapped(self, run_id: str) -> None:
        """将 agent 运行标记为 unmapped（cancel/reject 后等待 replay 复用）。

        与 unregister 不同，block 保留在 tracker 中以便 find_unmapped_running 发现。
        """
        state = self._by_run_id.pop(run_id, None)
        if state is None:
            return
        state.finalize_assistant_msg()
        state.tool_blocks.clear()
        self._unmapped.append(state)

    @property
    def active_run_ids(self) -> frozenset[str]:
        return frozenset(self._by_run_id)

    # ── 审批上下文 ──

    def set_approval_context(self, run_id: str) -> None:
        """标记某个 subagent 正在等待审批。"""
        self._approval_run_id = run_id

    def get_approval_block(self) -> "ToolBlock | None":
        """获取正在等待审批的 subagent 的 agent ToolBlock。"""
        if self._approval_run_id:
            state = self.get(self._approval_run_id)
            return state.agent_block if state else None
        return None

    def clear_approval_context(self) -> None:
        self._approval_run_id = None

    # ── Resume / Replay 支持 ──

    def prepare_for_resume(self) -> None:
        """resume 前调用：保留 agent blocks，移入 _unmapped 供 replay 匹配。

        replay 会产生新的 run_id，_handle_tool_start 会调用 remap() 重新关联。
        旧 run_id 通过 get() 的 fallback 查找仍能路由到对应的 SubagentState。
        """
        seen: set[int] = set()
        for state in self._by_run_id.values():
            sid = id(state)
            if sid not in seen:
                seen.add(sid)
                state.finalize_assistant_msg()
                state.tool_blocks.clear()
                self._unmapped.append(state)
        self._by_run_id.clear()
        self._approval_run_id = None

    def find_unmapped_running(self, args: dict | None = None) -> "ToolBlock | None":
        """查找尚未被真实 run_id 映射的 RUNNING agent ToolBlock。

        用于 replay 场景：新 run_id 到达时匹配已有的 block。
        优先通过工具参数精确匹配（并发 agent 可能以不同顺序 replay），
        无匹配时回退到第一个可用的 unmapped block。
        """
        from lumi.tui.widgets.tool_block import ToolStatus

        fallback: ToolBlock | None = None
        for state in self._unmapped:
            if state.agent_block.status == ToolStatus.RUNNING:
                if args and state.agent_block._args == args:
                    return state.agent_block
                if fallback is None:
                    fallback = state.agent_block
        return fallback

    def remap(self, new_run_id: str, agent_block: "ToolBlock") -> SubagentState | None:
        """将已有 block 关联到新的 run_id（replay 场景）。

        从 _unmapped 中找到目标 state 并移入 _by_run_id。
        """
        target: SubagentState | None = None
        for i, state in enumerate(self._unmapped):
            if state.agent_block is agent_block:
                target = self._unmapped.pop(i)
                break
        if target is None:
            return None
        target.run_id = new_run_id
        target.finalize_assistant_msg()
        target.tool_blocks.clear()
        target.pending_dom_clear = True
        self._by_run_id[new_run_id] = target
        return target

    # ── 生命周期 ──

    def reset(self) -> None:
        """run 结束时清空所有状态。"""
        self._by_run_id.clear()
        self._unmapped.clear()
        self._approval_run_id = None

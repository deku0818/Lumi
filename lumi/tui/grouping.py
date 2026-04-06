"""分组引擎 — 纯逻辑决策，无 widget 依赖。

决定每个工具调用应归入 ToolGroup、AgentGroup 还是独立挂载。
WidgetAssembler 根据决策结果操作 widget。
"""

from __future__ import annotations

from enum import StrEnum

from lumi.tui.widgets.tool_group import should_exclude_from_group
from lumi.utils.logger import logger


class GroupDecision(StrEnum):
    """工具调用的分组决策。"""

    STANDALONE = "standalone"  # 独立挂载到 ChatLog
    GROUP_FIRST = "group_first"  # 第一个 block，暂存等待后续
    GROUP_APPEND = "group_append"  # 追加到已有 ToolGroup
    AGENT = "agent"  # 归入 AgentGroup


class GroupingEngine:
    """纯逻辑的工具分组状态机。

    跟踪当前分组状态，为每个工具调用返回分组决策。
    不持有任何 widget 引用。

    同步契约（由 WidgetAssembler 保证）：
      - 每次 decide_tool() 之后必须调用 on_tool_started()
      - 每次清理 widget 分组状态后必须调用 flush_tools/flush_agents
    """

    __slots__ = (
        "_has_pending",
        "_has_active_group",
        "_has_agent_group",
        "_pending_decision",
    )

    def __init__(self) -> None:
        self._has_pending: bool = False
        self._has_active_group: bool = False
        self._has_agent_group: bool = False
        self._pending_decision: GroupDecision | None = None

    # ── 公开 API ──

    def decide_tool(self, name: str, approval_mode: bool) -> GroupDecision:
        """为一个工具调用返回分组决策。

        Args:
            name: 工具名称
            approval_mode: 是否处于审批模式
        """
        if self._pending_decision is not None:
            raise RuntimeError(
                f"decide_tool() called twice without on_tool_started(). "
                f"Pending decision: {self._pending_decision}"
            )
        if name == "agent":
            decision = GroupDecision.AGENT
        elif should_exclude_from_group(name, approval_mode):
            decision = GroupDecision.STANDALONE
        elif self._has_pending or self._has_active_group:
            decision = GroupDecision.GROUP_APPEND
        else:
            decision = GroupDecision.GROUP_FIRST
        self._pending_decision = decision
        return decision

    def on_tool_started(self, decision: GroupDecision) -> None:
        """决策被应用后更新内部状态。"""
        if self._pending_decision is None:
            raise RuntimeError("on_tool_started() called without prior decide_tool()")
        if self._pending_decision != decision:
            raise RuntimeError(
                f"on_tool_started({decision}) doesn't match "
                f"pending decision {self._pending_decision}"
            )
        self._pending_decision = None
        match decision:
            case GroupDecision.GROUP_FIRST:
                self._has_pending = True
            case GroupDecision.GROUP_APPEND:
                self._has_active_group = True
                self._has_pending = False
            case GroupDecision.AGENT:
                self._has_agent_group = True
            case GroupDecision.STANDALONE:
                pass

    def flush_tools(self) -> None:
        """重置工具分组状态（遇到文本/交互事件时调用）。"""
        if self._pending_decision is not None:
            logger.warning(
                "flush_tools() called with pending decision: %s "
                "(decide_tool → on_tool_started contract was broken)",
                self._pending_decision,
            )
        self._has_pending = False
        self._has_active_group = False
        self._pending_decision = None

    def flush_agents(self) -> None:
        """重置 agent 分组状态。"""
        self._has_agent_group = False

    def flush_all(self) -> None:
        """重置所有分组状态。"""
        self.flush_tools()
        self.flush_agents()

    def reset(self) -> None:
        """完全重置（新 run 开始时调用）。"""
        self._has_pending = False
        self._has_active_group = False
        self._has_agent_group = False
        self._pending_decision = None

    # ── 状态查询 ──

    @property
    def has_pending(self) -> bool:
        return self._has_pending

    @property
    def has_active_group(self) -> bool:
        return self._has_active_group

    @property
    def has_agent_group(self) -> bool:
        return self._has_agent_group

"""统一 widget 组装器 — 将 RenderItem 转换为 Textual widget 并挂载到 ChatLog。

EventRouter（live）和 message_restore（restore）均通过此组装器创建 widget，
消除两条路径中重复的分组/创建/挂载逻辑。

WidgetAssembler 与 GroupingEngine 之间的同步契约：
  每次调用 _grouping.decide_tool() 后必须调用 _grouping.on_tool_started()，
  每次 flush widget 状态后必须调用 _grouping.flush_tools/flush_agents。
  两者通过 _apply_tool_start / flush_groups / flush_all 方法保持同步。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lumi.tui.grouping import GroupDecision, GroupingEngine
from lumi.tui.render_items import (
    AgentEndItem,
    AgentStartItem,
    AssistantTextItem,
    FlushItem,
    RenderItem,
    ToolEndItem,
    ToolStartItem,
    UserItem,
)

if TYPE_CHECKING:
    from lumi.tui.widgets.agent_group import AgentGroup
    from lumi.tui.widgets.assistant_message import AssistantMessage
    from lumi.tui.widgets.chat_log import ChatLog
    from lumi.tui.widgets.tool_block import ToolBlock
    from lumi.tui.widgets.tool_group import ToolGroup

logger = logging.getLogger(__name__)


class WidgetAssembler:
    """统一的 widget 组装器。

    持有所有 widget 引用（pending_block、active_group 等），
    内部使用 GroupingEngine 做分组决策。
    """

    def __init__(self, chat_log: ChatLog) -> None:
        self._chat_log = chat_log
        self._grouping = GroupingEngine()

        # widget 引用（原 RunContext 中的字段）
        self._assistant_msg: AssistantMessage | None = None
        self._tool_blocks: dict[str, ToolBlock] = {}
        self._pending_block: ToolBlock | None = None
        self._active_group: ToolGroup | None = None
        self._agent_group: AgentGroup | None = None

    # ── 高层 API：RenderItem 驱动 ──

    async def apply_item(self, item: RenderItem) -> None:
        """处理单个 RenderItem，创建/挂载对应 widget。

        单个 item 处理失败不会中断后续 item，错误记录到日志。
        """
        try:
            match item:
                case UserItem():
                    await self._apply_user(item)
                case AssistantTextItem():
                    await self._apply_assistant_text(item)
                case ToolStartItem():
                    await self._apply_tool_start(item)
                case ToolEndItem():
                    await self._apply_tool_end(item)
                case AgentStartItem():
                    await self._apply_agent_start(item)
                case AgentEndItem():
                    await self._apply_agent_end(item)
                case FlushItem():
                    await self.flush_all()
        except Exception:
            logger.error(
                "Failed to apply RenderItem %s", type(item).__name__, exc_info=True
            )

    # ── 流式 API（live 路径专用）──

    async def append_stream_token(self, text: str) -> None:
        """追加流式 token 到当前 AssistantMessage（无则创建）。"""
        from lumi.tui.widgets.assistant_message import AssistantMessage

        await self.flush_groups()
        if self._assistant_msg is None:
            self._assistant_msg = AssistantMessage()
            await self._safe_mount(self._assistant_msg)
        self._assistant_msg.append_token(text)

    def finalize_assistant_msg(self) -> None:
        """结束当前流式 AssistantMessage。"""
        if self._assistant_msg is not None:
            self._assistant_msg.finalize()
            self._assistant_msg = None

    # ── widget 查询 API（EventRouter 需要）──

    @property
    def agent_group(self) -> AgentGroup | None:
        return self._agent_group

    @property
    def active_group(self) -> ToolGroup | None:
        return self._active_group

    @property
    def tool_blocks(self) -> dict[str, ToolBlock]:
        return self._tool_blocks

    @property
    def pending_block(self) -> ToolBlock | None:
        return self._pending_block

    @property
    def assistant_msg(self) -> AssistantMessage | None:
        return self._assistant_msg

    def pop_tool_block(self, key: str) -> ToolBlock | None:
        """弹出并返回指定 key 的 ToolBlock。"""
        return self._tool_blocks.pop(key, None)

    def find_tool_block_by_name(self, name: str) -> tuple[str, ToolBlock] | None:
        """按工具名查找 ToolBlock，返回 (key, block) 或 None。"""
        for k, b in list(self._tool_blocks.items()):
            if b._name == name:
                return k, b
        return None

    # ── 分组管理 ──

    async def flush_groups(self) -> None:
        """结束当前活跃的 ToolGroup（遇到文本/交互事件时调用）。

        若只有一个待合并的 block（尚未创建 ToolGroup），直接挂载到 ChatLog。
        """
        if self._pending_block is not None:
            await self._safe_mount(self._pending_block)
            self._pending_block = None

        if self._active_group is not None:
            self._active_group.finalize_group()
            self._active_group = None

        self._grouping.flush_tools()

    async def flush_all(self) -> None:
        """结束所有活跃分组。"""
        await self.flush_groups()
        self._grouping.flush_agents()

    def reset(self) -> None:
        """完全重置（新 run 或 /clear 时调用）。"""
        self._assistant_msg = None
        self._tool_blocks.clear()
        self._pending_block = None
        self._active_group = None
        self._agent_group = None
        self._grouping.reset()

    # ── 安全挂载 ──

    async def _safe_mount(self, widget: Any) -> bool:
        """安全地将 widget 挂载到 ChatLog，失败时记录错误。

        Returns:
            True 挂载成功，False 挂载失败。
        """
        try:
            await self._chat_log.mount(widget)
            return True
        except Exception:
            widget_desc = type(widget).__name__
            logger.error("Failed to mount %s to ChatLog", widget_desc, exc_info=True)
            return False

    # ── 内部实现 ──

    async def _apply_user(self, item: UserItem) -> None:
        from lumi.tui.widgets.user_message import UserMessage

        await self.flush_all()
        self.finalize_assistant_msg()
        await self._safe_mount(UserMessage(item.text))

    async def _apply_assistant_text(self, item: AssistantTextItem) -> None:
        from lumi.tui.widgets.assistant_message import AssistantMessage

        await self.flush_groups()
        msg = AssistantMessage()
        msg.append_token(item.text)
        await self._safe_mount(msg)
        if item.finalized:
            msg.finalize()
        else:
            self._assistant_msg = msg

    async def _apply_tool_start(self, item: ToolStartItem) -> None:
        from lumi.tui.widgets.tool_block import ToolBlock
        from lumi.tui.widgets.tool_group import ToolGroup

        decision = self._grouping.decide_tool(item.name, item.approval_mode)
        self._grouping.on_tool_started(decision)

        block = ToolBlock(item.name, item.args, approval_mode=item.approval_mode)
        self._tool_blocks[item.key] = block

        match decision:
            case GroupDecision.STANDALONE:
                await self.flush_groups()
                await self._safe_mount(block)

            case GroupDecision.GROUP_FIRST:
                self._pending_block = block

            case GroupDecision.GROUP_APPEND:
                if self._active_group is None:
                    # 第二个 block：创建 ToolGroup，加入之前暂存的 pending
                    group = ToolGroup()
                    self._active_group = group
                    await self._safe_mount(group)
                    pending = self._pending_block
                    self._pending_block = None
                    if pending is not None:
                        await group.add_block(pending, pending._name, pending._args)
                await self._active_group.add_block(block, item.name, item.args)

            case GroupDecision.AGENT:
                # agent 工具不走此路径（由 AgentStartItem 处理）
                logger.warning(
                    "ToolStartItem with agent name should use AgentStartItem"
                )

    async def _apply_tool_end(self, item: ToolEndItem) -> None:
        block = self._tool_blocks.pop(item.key, None)
        if block is None:
            # 按名称回退查找
            result = self.find_tool_block_by_name(item.name)
            if result is not None:
                key, block = result
                del self._tool_blocks[key]

        if block is None:
            logger.warning(
                "ToolEndItem dropped: no matching block "
                "(key=%s, name=%s, tracked_keys=%s)",
                item.key,
                item.name,
                list(self._tool_blocks.keys()),
            )
            return

        if item.is_error:
            block.set_error(item.output)
        else:
            block.set_done(item.output)

        if self._active_group is not None:
            self._active_group.notify_block_done(block)

    async def _apply_agent_start(self, item: AgentStartItem) -> None:
        from lumi.tui.widgets.agent_group import AgentGroup as AgentGroupCls

        if self._agent_group is None:
            await self.flush_groups()
            self.finalize_assistant_msg()
            group = AgentGroupCls()
            self._agent_group = group
            await self._safe_mount(group)

        self._agent_group.add_agent(item.run_id, item.agent_name, item.prompt)

    async def _apply_agent_end(self, item: AgentEndItem) -> None:
        if self._agent_group is None:
            logger.warning(
                "AgentEndItem without active AgentGroup (run_id=%s)", item.run_id
            )
            return

        if item.is_error:
            self._agent_group.finish_agent_error(item.run_id, item.output)
        else:
            self._agent_group.finish_agent(item.run_id, item.output)

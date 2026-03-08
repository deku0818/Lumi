"""TUI run 生命周期状态管理"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lumi.tui.widgets.assistant_message import AssistantMessage
    from lumi.tui.widgets.tool_block import ToolBlock


class RunPhase(StrEnum):
    """Agent run 的生命周期阶段"""

    IDLE = "idle"
    THINKING = "thinking"
    STREAMING = "streaming"
    TOOL_CALL_PENDING = "tool_call_pending"
    TOOL_RUNNING = "tool_running"
    WAITING_ASK = "waiting_ask"
    WAITING_APPROVAL = "waiting_approval"


@dataclass
class RunContext:
    """单次 run 的上下文，统一管理所有运行态变量"""

    phase: RunPhase = RunPhase.IDLE
    assistant_msg: AssistantMessage | None = None
    tool_blocks: dict[str, ToolBlock] = field(default_factory=dict)
    last_approval_tool_calls: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self.phase != RunPhase.IDLE

    def reset(self) -> None:
        self.phase = RunPhase.IDLE
        self.assistant_msg = None
        self.tool_blocks.clear()
        self.last_approval_tool_calls.clear()
        self.task = None

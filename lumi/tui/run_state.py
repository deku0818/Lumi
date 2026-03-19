"""TUI run 生命周期状态管理"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING, Protocol

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


class RenderState(Protocol):
    """渲染方法所需的最小状态接口。

    RunContext（主流程）和 SubagentState（子代理）均满足此协议。
    """

    assistant_msg: AssistantMessage | None
    tool_blocks: dict[str, ToolBlock]

    def finalize_assistant_msg(self) -> None: ...


@dataclass
class RunContext:
    """单次 run 的上下文，统一管理所有运行态变量"""

    phase: RunPhase = RunPhase.IDLE
    assistant_msg: AssistantMessage | None = None
    tool_blocks: dict[str, ToolBlock] = field(default_factory=dict)
    last_approval_tool_calls: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None

    # 计时和 token 跟踪（单次 run）
    _timer_origin: float = 0.0  # monotonic 起点（运行中）
    _timer_banked: float = 0.0  # 暂停前已累计的秒数
    _timer_paused: bool = False
    output_tokens: int = 0
    input_tokens: int = 0

    # 最近一次模型调用返回的 total_tokens
    total_tokens: int = 0
    # 缓存 token（cache_read + cache_creation）
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def finalize_assistant_msg(self) -> None:
        """结束当前流式 AssistantMessage。"""
        if self.assistant_msg is not None:
            self.assistant_msg.finalize()
            self.assistant_msg = None

    @property
    def is_running(self) -> bool:
        return self.phase != RunPhase.IDLE

    @property
    def elapsed(self) -> float:
        """返回累计运行秒数（暂停期间不增长）。"""
        if self._timer_origin == 0.0:
            return 0.0
        if self._timer_paused:
            return self._timer_banked
        return self._timer_banked + (monotonic() - self._timer_origin)

    def start(self) -> None:
        """标记运行开始，记录时间戳并重置 token 计数。"""
        self._timer_origin = monotonic()
        self._timer_banked = 0.0
        self._timer_paused = False
        self.output_tokens = 0
        self.input_tokens = 0

    def pause_timer(self) -> None:
        """暂停计时（等待用户交互时调用）。"""
        if not self._timer_paused and self._timer_origin:
            self._timer_banked += monotonic() - self._timer_origin
            self._timer_paused = True

    def resume_timer(self) -> None:
        """恢复计时。"""
        if self._timer_paused:
            self._timer_origin = monotonic()
            self._timer_paused = False

    def count_stream_token(self) -> None:
        """每收到一个 STREAM_TOKEN 事件调用，近似 +1 output token（仅用于状态栏显示）。"""
        self.output_tokens += 1

    def accumulate_usage(self, usage: dict | None) -> None:
        """从流式 usage_metadata 更新单次 run 的 token 计数（取 max 修正近似值）。"""
        if not usage:
            return
        input_val = usage.get("input_tokens", 0)
        output_val = usage.get("output_tokens", 0)
        if input_val:
            self.input_tokens = max(self.input_tokens, input_val)
        if output_val:
            self.output_tokens = max(self.output_tokens, output_val)
        # cache 详情（流式阶段也可能携带）
        details = usage.get("input_token_details") or {}
        cr = details.get("cache_read", 0) or 0
        cc = details.get("cache_creation", 0) or 0
        if cr:
            self.cache_read_tokens = max(self.cache_read_tokens, cr)
        if cc:
            self.cache_creation_tokens = max(self.cache_creation_tokens, cc)

    def commit_model_usage(self, usage: dict | None) -> None:
        """MODEL_END 时调用：记录最新的 total_tokens 和 cache 详情（直接覆盖，不累加）。"""
        if usage:
            total_val = usage.get("total_tokens", 0)
            if total_val:
                self.total_tokens = total_val
            else:
                self.total_tokens = self.input_tokens + self.output_tokens
            # 提取 cache 详情
            details = usage.get("input_token_details") or {}
            self.cache_read_tokens = details.get("cache_read", 0) or 0
            self.cache_creation_tokens = details.get("cache_creation", 0) or 0
        else:
            self.total_tokens = self.input_tokens + self.output_tokens

    def reset(self) -> None:
        """重置单次 run 状态（保留会话级 token 计数供 StatusLine 显示）。"""
        self.phase = RunPhase.IDLE
        self.assistant_msg = None
        self.tool_blocks.clear()
        self.last_approval_tool_calls.clear()
        self.task = None
        self._timer_origin = 0.0
        self._timer_banked = 0.0
        self._timer_paused = False

    def reset_session(self) -> None:
        """重置会话级计数（/clear 时调用）。"""
        self.reset()
        self.output_tokens = 0
        self.input_tokens = 0
        self.total_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0

"""渲染中间表示 — 纯数据类型，无 Textual 依赖。

EventRouter（live 路径）和 message_restore（restore 路径）
均将消息转换为 RenderItem，由 WidgetAssembler 统一组装为 widget。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UserItem:
    """用户消息。"""

    text: str


@dataclass(frozen=True)
class AssistantTextItem:
    """助手文本消息（restore 路径使用，finalized=True 立即渲染 Markdown）。"""

    text: str
    finalized: bool = True


@dataclass(frozen=True)
class ToolStartItem:
    """工具调用开始。"""

    key: str  # tool_call_id 或 name
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    approval_mode: bool = False


@dataclass(frozen=True)
class ToolEndItem:
    """工具调用结束。"""

    key: str
    name: str
    output: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class AgentStartItem:
    """子 agent 工具调用开始。"""

    run_id: str
    agent_name: str
    prompt: str = ""


@dataclass(frozen=True)
class AgentEndItem:
    """子 agent 工具调用结束。"""

    run_id: str
    output: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class FlushItem:
    """信号：要求 assembler 结束当前活跃的分组。"""


RenderItem = (
    UserItem
    | AssistantTextItem
    | ToolStartItem
    | ToolEndItem
    | AgentStartItem
    | AgentEndItem
    | FlushItem
)

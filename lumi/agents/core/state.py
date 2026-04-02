from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from lumi.agents.tools.permissions.engine import PermissionEngine

from langgraph.graph.message import add_messages

from dataclasses import dataclass, field


class SummaryData(TypedDict, total=False):
    """摘要数据结构"""

    summarized_ids: list[str]
    summary_text: str


@dataclass
class LumiAgentContext:
    tools: list = field(default_factory=list)
    system_prompt: str = field(default="")
    model_name: str = field(default="")
    permission_engine: "PermissionEngine | None" = field(default=None)
    """PermissionEngine 实例，用于工具权限评估"""


class LumiAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    agent_outcome: dict
    iterations: int
    tool_mode: Literal["auto", "privileged"]
    """工具审批模式:
    - "auto": 权限引擎评估，未通过则由 TUI 询问用户审批
    - "privileged": 权限引擎评估但自动放行，仅 bypass-immune 仍需审批
    """
    todos: NotRequired[list]
    """任务列表，用于追踪复杂任务的执行进度"""
    summary: SummaryData
    """摘要信息"""
    output_schema: NotRequired[dict[str, Any]]
    """结构化输出的 JSON Schema"""
    output_enrich: NotRequired[list[dict[str, Any]]]
    """结构化输出附加数据规则"""
    structured_output: NotRequired[dict[str, Any]]
    """结构化输出结果"""
    tool_cancelled: NotRequired[bool]
    """工具执行被用户取消时置 True，供条件边路由到 END"""
    execution_mode: NotRequired[str]
    """执行模式: "normal"(默认) | "plan" | "readonly" | 自定义模式
    非 "normal" 时 is_use_tool 路由会根据对应 ModePolicy 拦截不允许的工具调用。
    """

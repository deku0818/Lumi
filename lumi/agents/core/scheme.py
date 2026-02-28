from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from dataclasses import dataclass, field


@dataclass
class LumiAgentContext:
    tools: list = field(default_factory=list)
    system_prompt: str = field(default="")
    model_name: str = field(default="")


class LumiAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    agent_outcome: dict
    iterations: int
    tool_mode: Literal["auto", "reject", "approve"]
    todos: NotRequired[list]
    """任务列表，用于追踪复杂任务的执行进度"""
    summary: dict
    """摘要信息，格式：{summarized_ids: list, summary_text: str, system_msg_id: str | None}"""
    output_schema: NotRequired[dict[str, Any]]
    """结构化输出的 JSON Schema"""
    output_enrich: NotRequired[list[dict[str, Any]]]
    """结构化输出附加数据规则"""
    structured_output: NotRequired[dict[str, Any]]
    """结构化输出结果"""

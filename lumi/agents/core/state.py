from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from lumi.agents.permissions.engine import PermissionEngine
    from lumi.gateway.bridge.broker import ApprovalBroker

from dataclasses import dataclass, field

from langgraph.graph.message import add_messages


@dataclass
class LumiAgentContext:
    tools: list = field(default_factory=list)
    system_prompt: str = field(default="")
    model_name: str = field(default="")
    """模型名；连接（base_url / api_key）由 create_llm 按供应商 profile 解析。"""
    permission_engine: PermissionEngine | None = field(default=None)
    """PermissionEngine 实例，用于工具权限评估"""
    tool_mode: str = field(default="default")
    """工具审批模式（运行时真相源，所有节点共享同一 context 实例，bridge 侧可随时改）:
    - "default": 权限引擎评估，未通过则由 TUI 询问用户审批
    - "accept_edits": 文件编辑工具(write/edit)在工作区内自动放行，bash 等仍需审批
    - "privileged": 权限引擎评估但自动放行，仅 bypass-immune 仍需审批
    - "auto": AI 审批模式——本该问人的批次交分类器(AutoClassify 节点)裁决
      approve/ask/reject；DENY 与 bypass-immune 仍免疫，强制走人工审批
    放在 context（而非 state）：state 是每个 super-step 的快照，运行中改不动；context
    是共享可变引用，bridge 改它后下一个节点 runtime.context 立即读到 → 支持运行中实时切换。"""
    approval_broker: ApprovalBroker | None = field(default=None)
    """在途审批 Broker，由 bridge 在 create_agent 后注入（与 permission_engine 同源）。
    节点 / ask 工具经它 await 审批，替代 interrupt() 中断-恢复。子 agent 由 agent 工具
    从父 context 传播。无 bridge 的纯 graph 调用（headless）保持 None。"""
    memory_enabled: bool = field(default=False)
    """是否为本 agent 注入持久记忆（MEMORY.md 索引 + 系统提示词行为说明）。
    默认 False（opt-in），与 create_agent 一致；仅 bridge 的主对话 agent 置 True。
    项目说明 LUMI.md 不受此开关影响，主/子 agent 均注入。"""


class LumiAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    iterations: int
    todos: NotRequired[list]
    """任务列表，用于追踪复杂任务的执行进度"""
    output_schema: NotRequired[dict[str, Any]]
    """结构化输出的 JSON Schema"""
    output_enrich: NotRequired[list[dict[str, Any]]]
    """结构化输出附加数据规则"""
    structured_output: NotRequired[dict[str, Any]]
    """结构化输出结果"""
    tool_cancelled: NotRequired[bool]
    """工具执行被用户取消时置 True，供条件边路由到 END"""
    depth: NotRequired[int]
    """子 agent 委派深度：主 agent 为 0，每委派一层 +1。
    agent 工具据此限制最大委派层数（见 agents.max_delegation_depth）。"""
    execution_mode: NotRequired[str]
    """执行模式: "normal"(默认) | "plan" | "readonly" | 自定义模式
    非 "normal" 时 is_use_tool 路由会根据对应 ModePolicy 拦截不允许的工具调用。
    """

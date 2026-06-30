"""Hook 框架的数据契约：事件枚举、上下文、返回值类型、按事件 payload 约定。

设计原则：
- ``HookEvent`` 是事件名 Literal；新增事件必须同步在 graph 对应位置加
  ``dispatch_hooks`` 调用，否则注册了也永不触发。
- ``HookContext`` 不可变，hook 只读；``payload`` 字段按 ``event`` 不同，键由
  下面的 ``*Payload`` TypedDict 约定。运行时不强校验，IDE 补全靠它。
- ``HookResult = None | Command | AdditionalContext | Block``——后两者是常用
  软扩展糖，``dispatch.py`` 内部翻译成 ``Command``。Hook 写者不必手拼
  ``HumanMessage(content=[{type:text, text: <system-reminder>...}])``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

if TYPE_CHECKING:
    from lumi.agents.core.state import LumiAgentState


HookEvent = Literal[
    "Stop",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
]


@dataclass(frozen=True)
class HookContext:
    """Hook 入参：state + config + event + 按事件填的 payload。

    SessionStart 阶段 ``state`` 可能为 ``{}``（graph 还没跑），hook 必须容忍空
    state 这一情况，否则会在 SessionStart 路径上崩。
    """

    state: LumiAgentState
    config: RunnableConfig
    event: HookEvent
    payload: dict[str, Any] = field(default_factory=dict)
    runtime: Any = None
    """LangGraph ``Runtime[LumiAgentContext]``，由 ``on_agent_stop`` 传入；Stop hook 经它取
    ``context``（system_prompt / permission_engine / memory_enabled）。其余事件可为 None。"""


@dataclass(frozen=True)
class AdditionalContext:
    """语法糖：往对话流注入一段 ``<system-reminder>`` 让模型继续干活。

    ``dispatch.py`` 把 ``AdditionalContext("xxx")`` 翻译为
    ``Command(goto=default_goto, update={"messages": [HumanMessage(content=[
    {type:text, text:<system-reminder>xxx</system-reminder>}])]})``。
    ``default_goto`` 由各事件调用点按节点下游边传入（不要硬编码 CallModel）。
    """

    text: str


@dataclass(frozen=True)
class Block:
    """语法糖：拒绝执行 + 让消息流以 ``reason`` 收尾。

    翻译为 ``Command(goto=END, update={"messages": [AIMessage(content=reason)]})``。
    PreToolUse 调用点会额外补齐 ``ToolMessage(status="error")`` 配对，避免
    残留的 tool_call 把 AIMessage 整条剪掉。
    """

    reason: str


HookResult = Command | AdditionalContext | Block | None
"""Hook 函数返回值。``None`` 放行；其余三种由 dispatch 翻译为 ``Command``。"""

Hook = Callable[["HookContext"], Awaitable[HookResult]]
"""Hook 函数签名。``async (HookContext) -> HookResult``。

各事件的 ``payload`` 形状（运行时就是 ``dict[str, Any]``）由 graph 中对应的
``dispatch_hooks`` 调用点构造，约定见各调用点；不另立 TypedDict 以免与真实形状漂移。"""

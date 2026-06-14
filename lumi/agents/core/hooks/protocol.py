"""Shell hook 的决策 JSON 协议。

输入（喂给外部命令的 stdin）：
::

    {
      "version": 1,
      "event": "PreToolUse",
      "thread_id": "abc-123",
      "payload": {...},
      "messages_tail": [...]
    }

输出（外部命令的 stdout）：
::

    {
      "decision": "allow" | "deny" | "passthrough",
      "additionalContext": "string",
      "stopReason": "string"
    }

字段冻结策略：``version: 1`` 保留向后兼容；framework 容忍未知字段（仅 warn）；
删字段是 breaking。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from lumi.agents.core.hooks.schema import (
    AdditionalContext,
    Block,
    HookContext,
    HookEvent,
    HookResult,
)
from lumi.agents.core.node_helpers.messages import content_to_str
from lumi.utils.logger import logger

PROTOCOL_VERSION = 1
"""协议版本。新增字段不动它；删字段或语义变更要 bump。"""

DEFAULT_MESSAGES_TAIL = 10
"""默认透传给外部 hook 的尾部消息条数。避免子进程吞超大 JSON。"""

Decision = Literal["allow", "deny", "passthrough"]

DECISION_DENY: Decision = "deny"
DECISION_ALLOW: Decision = "allow"
DECISION_PASSTHROUGH: Decision = "passthrough"

TOOL_FILTERED_EVENTS: frozenset[HookEvent] = frozenset({"PreToolUse", "PostToolUse"})
"""``matcher`` 字段仅在这些事件下按 ``tool_calls[*].name`` 筛选。"""


def matches_tool_filter(
    pattern: re.Pattern[str] | None,
    event: HookEvent,
    payload: dict[str, Any],
) -> bool:
    """matcher 命中判定。

    - ``pattern is None``：无筛选条件，总是命中
    - ``event`` 不在 ``TOOL_FILTERED_EVENTS``：matcher 无效，总是命中
    - 否则：当且仅当 ``payload["tool_calls"][*].name`` 任一被 pattern 匹配时命中
    """
    if pattern is None:
        return True
    if event not in TOOL_FILTERED_EVENTS:
        return True
    tool_calls = payload.get("tool_calls") or []
    return any(pattern.search(tc.get("name", "")) for tc in tool_calls)


def warn_matcher_unused(event: HookEvent, matcher: str | None, label: str) -> None:
    """``matcher`` 仅 PreToolUse / PostToolUse 生效——其他事件配了也是死字段。"""
    if matcher is not None and event not in TOOL_FILTERED_EVENTS:
        logger.warning(
            "[hooks] %s 事件下 matcher=%r 无效（仅 PreToolUse/PostToolUse 生效），已忽略",
            event,
            matcher,
        )


_KNOWN_OUTPUT_FIELDS = frozenset(
    {"decision", "additionalContext", "stopReason", "version"}
)


def _serialize_message(msg: BaseMessage) -> dict[str, Any]:
    """LangChain 消息 → 精简 dict。剥离 multimodal binary blob 防体积爆炸。"""
    role_map = {HumanMessage: "user", AIMessage: "assistant", ToolMessage: "tool"}
    role = next(
        (r for cls, r in role_map.items() if isinstance(msg, cls)),
        type(msg).__name__,
    )
    # content_to_str 复用核心层的多模态展平（text 原样，image/document 转占位防 base64 泄漏）
    out: dict[str, Any] = {"role": role, "content": content_to_str(msg.content)}
    if isinstance(msg, AIMessage) and msg.tool_calls:
        out["tool_calls"] = [
            {"name": tc["name"], "id": tc.get("id"), "args": tc.get("args", {})}
            for tc in msg.tool_calls
        ]
    if isinstance(msg, ToolMessage):
        out["tool_call_id"] = msg.tool_call_id
        out["name"] = msg.name
        if getattr(msg, "status", None):
            out["status"] = msg.status
    return out


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """payload 里可能塞了 LangChain Message 对象（PostToolUse 的 tool_messages），转 dict。"""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, BaseMessage):
            out[k] = _serialize_message(v)
        elif isinstance(v, list):
            out[k] = [
                _serialize_message(item) if isinstance(item, BaseMessage) else item
                for item in v
            ]
        else:
            out[k] = v
    return out


def serialize_input(
    event: HookEvent,
    ctx: HookContext,
    *,
    messages_tail: int = DEFAULT_MESSAGES_TAIL,
) -> str:
    """把 HookContext 序列化为外部 hook 的输入 JSON 字符串。"""
    configurable = ctx.config.get("configurable", {}) if ctx.config else {}
    raw_messages = list(ctx.state.get("messages") or []) if ctx.state else []
    tail = raw_messages[-messages_tail:] if messages_tail > 0 else []
    body = {
        "version": PROTOCOL_VERSION,
        "event": event,
        "thread_id": configurable.get("thread_id"),
        "payload": _sanitize_payload(ctx.payload),
        "messages_tail": [
            _serialize_message(m) for m in tail if isinstance(m, BaseMessage)
        ],
    }
    return json.dumps(body, ensure_ascii=False, default=str)


def parse_output(raw: str, *, source: str) -> HookResult:
    """解析外部 hook 的输出 JSON，翻译为 HookResult。

    - ``decision: "deny"`` → ``Block(reason=stopReason or additionalContext or 默认)``
    - ``decision: "allow"`` / ``"passthrough"`` / 缺省 → 不阻断
    - ``additionalContext: "..."`` → ``AdditionalContext(text=...)``
    - deny 优先于 additionalContext；未知字段 warn 但不报错；解析失败返 None（passthrough）

    ``source`` 用于日志（如 "shell:/path/to/hook.sh"）。
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "[hooks] %s output is not JSON; treated as passthrough: %r",
            source,
            raw[:200],
        )
        return None
    if not isinstance(body, dict):
        logger.warning(
            "[hooks] %s output is not JSON object; treated as passthrough", source
        )
        return None

    unknown = set(body.keys()) - _KNOWN_OUTPUT_FIELDS
    if unknown:
        logger.warning(
            "[hooks] %s output has unknown fields %s; ignored", source, unknown
        )

    decision = body.get("decision")
    additional = body.get("additionalContext")
    stop_reason = body.get("stopReason")

    if decision == DECISION_DENY:
        reason = stop_reason or additional or "blocked by hook"
        return Block(str(reason))
    if isinstance(additional, str) and additional.strip():
        return AdditionalContext(additional)
    return None

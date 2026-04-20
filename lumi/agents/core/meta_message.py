"""Meta message 标记契约 — 工具/系统生成的不展示给用户的 HumanMessage。

meta 消息是 "给模型看的,不是用户说的话" —— 常见来源:
- 工具通过 Command(update={"messages": [ToolMessage, HumanMessage(...)]})
  注入多模态 content block(例如 read 工具读图/读 PDF)
- 后台任务完成通知由 AgentBridge 注入

TUI 的 should_show_human_message / session_store 会基于 ``META_KEY`` 过滤,
避免把这类消息当成真实用户气泡渲染。任何新增 meta HumanMessage 的调用点
都必须经由 ``meta_human_message()`` 或 ``META_KEY`` 常量,
防止字段名拼写漂移导致过滤静默失效。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

META_KEY = "is_meta"
"""HumanMessage.additional_kwargs 中标记 meta 消息的键名。"""


def meta_human_message(content: str | list[dict[str, Any]]) -> HumanMessage:
    """构造带 is_meta 标记的 HumanMessage。"""
    return HumanMessage(content=content, additional_kwargs={META_KEY: True})


def is_meta_message(msg: object) -> bool:
    """判断 HumanMessage(或等效 dict)是否为 meta 消息。"""
    if isinstance(msg, dict):
        extra = msg.get("additional_kwargs") or {}
    else:
        extra = getattr(msg, "additional_kwargs", None) or {}
    return bool(extra.get(META_KEY))

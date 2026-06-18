"""Meta message 标记契约 — 工具/系统生成的不展示给用户的 HumanMessage。

meta 消息是 "给模型看的,不是用户说的话" —— 常见来源:
- 工具通过 Command(update={"messages": [ToolMessage, HumanMessage(...)]})
  注入多模态 content block(例如 read 工具读图/读 PDF)
- 后台任务完成通知由 AgentBridge 注入

lumi.sessions 的 should_show_human_message / session_store 会基于 ``META_KEY`` 过滤,
避免把这类消息当成真实用户气泡渲染。任何新增 meta HumanMessage 的调用点
都必须经由 ``meta_human_message()`` 或 ``META_KEY`` 常量,
防止字段名拼写漂移导致过滤静默失效。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.messages import HumanMessage

META_KEY = "is_meta"
"""HumanMessage.additional_kwargs 中标记 meta 消息的键名。"""

REMINDER_KEY = "is_hook_reminder"
"""标记"hook 注入的 system-reminder"的键名。

reminder 是 meta 的一个**子类**：既不渲染为用户气泡（is_meta），又是**轮内合成
插话、不是轮边界**——区别于后台任务通知等"真实 meta 消息"（它们是模型要响应的
新输入，构成轮边界）。结构化输出的连续失败计数 / 拉回计数 / accepted 判定都靠
``is_reminder_message`` 精确跳过 reminder，而不是泛跳过所有 is_meta，否则后台通知
会被误当成 reminder 跳过、导致跨轮泄漏计数。"""


def meta_human_message(content: str | list[dict[str, Any]]) -> HumanMessage:
    """构造带 is_meta 标记的 HumanMessage。"""
    return HumanMessage(content=content, additional_kwargs={META_KEY: True})


def reminder_human_message(content: str | list[dict[str, Any]]) -> HumanMessage:
    """构造 hook 注入的 system-reminder：同时带 is_meta + is_hook_reminder 标记。"""
    return HumanMessage(
        content=content, additional_kwargs={META_KEY: True, REMINDER_KEY: True}
    )


def _additional_kwargs(msg: object) -> dict:
    if isinstance(msg, dict):
        return msg.get("additional_kwargs") or {}
    return getattr(msg, "additional_kwargs", None) or {}


def is_meta_message(msg: object) -> bool:
    """判断 HumanMessage(或等效 dict)是否为 meta 消息。"""
    return bool(_additional_kwargs(msg).get(META_KEY))


def is_reminder_message(msg: object) -> bool:
    """判断是否为 hook 注入的 system-reminder（轮内合成插话，不是轮边界）。"""
    return bool(_additional_kwargs(msg).get(REMINDER_KEY))


def iter_current_turn(messages: list) -> Iterator[Any]:
    """从尾部 yield 本轮消息（新→旧），到第一条**真实** HumanMessage 为止（不含它）。

    hook reminder（``is_hook_reminder``）是轮内合成插话——跳过（仍 yield）继续上溯；
    后台任务通知等真实 meta 是模型要响应的新输入、构成轮边界，遇到即停。

    把"本轮窗口"边界判定收在一处，供结构化输出的连续失败计数 / 拉回计数 / accepted
    判定共用——避免各扫描器各自重复倒扫骨架、且漏掉 reminder 跳过导致跨轮泄漏。
    """
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage) and not is_reminder_message(msg):
            return
        yield msg

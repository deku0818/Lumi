"""消息可见性判定 — 集中管理哪些 HumanMessage 应在 TUI 中显示。

restore 和 session_store 统一调用 should_show_human_message()，
避免可见性逻辑散落在各处。
"""

from __future__ import annotations


def should_show_human_message(msg: object) -> bool:
    """判断 HumanMessage 是否应在 restore / session 列表中显示。

    通过 additional_kwargs["is_meta"] 标记判定。
    发送侧负责在系统生成的消息上设置此标记。

    Args:
        msg: LangChain Message 对象或等效字典。
    """
    if isinstance(msg, dict):
        extra = msg.get("additional_kwargs") or {}
    else:
        extra = getattr(msg, "additional_kwargs", None) or {}
    return not extra.get("is_meta")

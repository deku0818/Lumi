"""token 用量提取：流式事件与历史快照共用一个口径。"""

from __future__ import annotations


def extract_usage(obj) -> dict | None:
    """LangChain 消息 / chunk 的 ``usage_metadata``（TypedDict，即 dict）；缺失或空返回 None。"""
    return getattr(obj, "usage_metadata", None) or None


def last_ai_usage(state) -> dict | None:
    """graph state 末条带 usage 的消息的用量——某些 API 在 state 里保留了比流式聚合
    更完整的 usage（如 cache 详情），turn.complete 与 load_history 都取它。"""
    messages = (state.values or {}).get("messages", [])
    for msg in reversed(messages):
        if getattr(msg, "usage_metadata", None) is not None:
            return extract_usage(msg)
    return None

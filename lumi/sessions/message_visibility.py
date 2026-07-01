"""消息可见性判定 — 集中管理哪些 HumanMessage 应在会话列表/历史中显示。

WS 历史恢复与 session_store 统一调用 should_show_human_message()，
避免可见性逻辑散落在各处。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.meta_message import is_meta_message


def should_show_human_message(msg: object) -> bool:
    """判断 HumanMessage 是否应在 restore / session 列表中显示。

    通过 ``META_KEY`` 标记判定(见 ``lumi.agents.core.meta_message``)。
    发送侧应通过 ``meta_human_message()`` 或 ``META_KEY`` 常量设置此标记。

    Args:
        msg: LangChain Message 对象或等效字典。
    """
    return not is_meta_message(msg)


def _is_human_message(m: object) -> bool:
    """human 消息类型判定，兼容 LangChain 对象与 dict 格式。

    与 ``session_store._extract_first_human_message`` 一致——checkpoint 恢复路径的 messages
    可能是对象或 ``{"type": "human", ...}`` dict。
    """
    if isinstance(m, HumanMessage):
        return True
    return isinstance(m, dict) and m.get("type") == "human"


def count_human_messages(messages: list) -> int:
    """数真实用户消息数（human 类型且非 meta/reminder 注入）。

    供 dream 的 human 门度量「内容量」——排除工具/系统生成的 meta HumanMessage。
    """
    return sum(
        1 for m in messages if _is_human_message(m) and should_show_human_message(m)
    )

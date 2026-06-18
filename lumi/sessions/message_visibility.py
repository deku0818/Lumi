"""消息可见性判定 — 集中管理哪些 HumanMessage 应在会话列表/历史中显示。

WS 历史恢复与 session_store 统一调用 should_show_human_message()，
避免可见性逻辑散落在各处。
"""

from __future__ import annotations

from lumi.agents.core.meta_message import is_meta_message


def should_show_human_message(msg: object) -> bool:
    """判断 HumanMessage 是否应在 restore / session 列表中显示。

    通过 ``META_KEY`` 标记判定(见 ``lumi.agents.core.meta_message``)。
    发送侧应通过 ``meta_human_message()`` 或 ``META_KEY`` 常量设置此标记。

    Args:
        msg: LangChain Message 对象或等效字典。
    """
    return not is_meta_message(msg)

"""消息可见性判定 — 集中管理哪些 HumanMessage 应在会话列表/历史中显示。

WS 历史恢复与 session_store 统一调用 should_show_human_message()，
避免可见性逻辑散落在各处。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.meta_message import is_meta_message
from lumi.utils.constants import LUMI_META_KEY


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


def latest_human_ts(messages: list) -> float:
    """真实用户消息（非 meta 注入）的最新落库时刻，epoch 秒；一条带 ts 的都没有返 0.0。

    ts 由 ``stream_response`` 落库时写入 ``additional_kwargs["lumi"]["ts"]``（本机时钟、
    毫秒）。供 dream 判定「自上次综合以来有无新内容」——基于时间戳而非消息计数，
    compact 增删历史不影响判定（压缩载体无 ts，天然不计）。
    """
    latest = 0
    for m in messages:
        if not (_is_human_message(m) and should_show_human_message(m)):
            continue
        if isinstance(m, dict):
            kwargs = m.get("additional_kwargs") or {}
        else:
            kwargs = getattr(m, "additional_kwargs", None) or {}
        latest = max(latest, (kwargs.get(LUMI_META_KEY) or {}).get("ts", 0))
    return latest / 1000

"""消息可见性判定 — 集中管理哪些 HumanMessage 应在会话列表/历史中显示。

WS 历史恢复与 session_store 统一调用 should_show_human_message()，
避免可见性逻辑散落在各处。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.meta_message import declared_items, message_ts


def should_show_human_message(msg: object) -> bool:
    """判断 HumanMessage 是否应在 restore / session 列表中显示。

    按显示声明判定（见 ``lumi.agents.core.meta_message``）：``items`` 已声明 →
    非空即显示（``[]`` = 合成消息，不显示）；未声明（cron / 子 agent 等
    不经 bridge 的构造点）→ 显示，文本走 fallback。

    Args:
        msg: LangChain Message 对象或等效字典。
    """
    items = declared_items(msg)
    return bool(items) if items is not None else True


def is_human_message(m: object) -> bool:
    """human 消息类型判定，兼容 LangChain 对象与 dict 格式——checkpoint 恢复
    路径的 messages 可能是对象或 ``{"type": "human", ...}`` dict。
    session_store 与 latest_human_ts 共用，双形态判定的单一实现。"""
    if isinstance(m, HumanMessage):
        return True
    return isinstance(m, dict) and m.get("type") == "human"


def latest_human_ts(messages: list) -> float:
    """真实用户消息的最新落库时刻，epoch 秒；一条带 ts 的都没有返 0.0。

    ts 由 bridge 构造真实用户消息时写入 ``additional_kwargs["lumi"]["ts"]``（本机时钟、
    毫秒），其余合成消息（reminder / 后台通知 / 工具回灌）一律不带——故判据即「human
    且带 ts」。供 dream 判定「自上次综合以来有无新内容」：基于时间戳而非消息计数，且
    压缩把真人消息删光时由摘要 carrier 继承该时刻（见 ``build_summary_carrier``），
    判活基线不随压缩归零。
    """
    return (
        max((message_ts(m) for m in messages if is_human_message(m)), default=0) / 1000
    )

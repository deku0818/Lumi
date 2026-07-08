"""摘要 carrier 构造。

在线（summarizer 节点）与离线（build_compacted_update）压缩共用：摘要作为一条
独立的 carrier 消息存在，不注入进用户消息。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.meta_message import synthetic_human_message


def format_summary_block(summary_text: str) -> str:
    """将摘要文本包裹为 ``<summary>`` 标签块。"""
    return f"<summary>\n{summary_text}\n</summary>\n"


def build_summary_carrier(summary_text: str) -> HumanMessage:
    """摘要 carrier：声明无可显示的合成消息（不渲染为用户气泡），
    在线/离线压缩共用——carrier 形态的单一真源。"""
    return synthetic_human_message(format_summary_block(summary_text))

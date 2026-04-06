"""摘要注入模块

将对话摘要格式化为 ``<summary>`` 标签块并注入到用户消息中。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import inject_text_into_message


def format_summary_block(summary_text: str) -> str:
    """将摘要文本包裹为 ``<summary>`` 标签块。"""
    return f"<summary>\n{summary_text}\n</summary>\n"


def inject_summary_into_message(
    message: HumanMessage, summary_text: str
) -> HumanMessage:
    """将格式化的摘要块注入到用户消息 content 最前面，返回新消息。"""
    return inject_text_into_message(message, format_summary_block(summary_text))

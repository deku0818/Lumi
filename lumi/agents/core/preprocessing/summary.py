"""摘要注入模块

将对话摘要格式化为 <summary> 标签块并注入到用户消息中。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import inject_text_into_message


def format_summary_block(summary_text: str) -> str:
    """格式化为 <summary> 标签块。

    Args:
        summary_text: 摘要文本

    Returns:
        包含 <summary> 标签的格式化文本
    """
    return f"<summary>\n{summary_text}\n</summary>\n"


def inject_summary_into_message(
    message: HumanMessage, summary_text: str
) -> HumanMessage:
    """将摘要注入到用户消息中。

    调用通用 inject_text_into_message 将格式化后的摘要块
    插入到用户消息 content 最前面。

    Args:
        message: 原始用户消息
        summary_text: 摘要文本

    Returns:
        注入摘要后的新 HumanMessage
    """
    block = format_summary_block(summary_text)
    return inject_text_into_message(message, block)

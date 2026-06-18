"""消息文本提取 — 从 LangChain 消息 content 中取纯文本/用户可读文本。

无 textual 依赖，供 WS 服务端（load_history）提取历史消息文本。
"""

from __future__ import annotations

from lumi.sessions.text_cleaning import extract_display_text


def extract_text_content(content: str | list) -> str:
    """从消息 content 中提取纯文本。

    支持 str 和 list[dict] 两种 LangChain 消息格式。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def extract_human_display_text(content: str | list) -> str:
    """从 human 消息中提取用于显示的文本。

    技能命令消息从 <command-name> 和 <user-input> 标签还原用户输入，
    如 "/media-digest 介绍下这个"。
    非技能消息则过滤掉所有注入块（system-reminder、summary、command-*），
    返回剩余纯文本。
    """
    return extract_display_text(extract_text_content(content))

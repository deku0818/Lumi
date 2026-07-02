"""消息文本提取 — 从 LangChain 消息 content 中取纯文本/用户可读文本。

无 textual 依赖，供 WS 服务端（load_history）提取历史消息文本。
"""

from __future__ import annotations


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


def _tool_call_name(tc) -> str:
    """工具调用名，兼容 dict（标准 tool_call）与 ToolCall 对象（某些反序列化路径）。"""
    if isinstance(tc, dict):
        return tc.get("name") or "?"
    return getattr(tc, "name", None) or "?"


def extract_messages_as_text(messages: list) -> str:
    """把消息列表导出成扁平文本，一行一消息，供 dream 的 grep 语料。

    格式：``[user] …`` / ``[assistant] …`` / ``[assistant→tool:NAME] …``（带工具调用）/
    ``[tool:NAME] …``（工具结果）。消息内换行折叠为 ``⏎`` 保证每条恰好一行（grep 友好）；
    system 消息跳过。比 ``messages_to_dict`` 的嵌套 JSON 对窄关键词 grep 友好得多。
    """
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "type", "")
        if role == "system":
            continue
        text = (
            extract_text_content(getattr(m, "content", "")).replace("\n", "⏎").strip()
        )
        if role == "human":
            tag = "user"
        elif role == "ai":
            tool_calls = getattr(m, "tool_calls", None) or []
            if tool_calls:
                names = ",".join(_tool_call_name(tc) for tc in tool_calls)
                tag = f"assistant→tool:{names}"
            else:
                tag = "assistant"
        elif role == "tool":
            tag = f"tool:{getattr(m, 'name', None) or '?'}"
        else:
            tag = role or "?"
        # assistant 调工具时 content 可能为空，仍保留行以标注调了什么
        if text or role == "ai":
            lines.append(f"[{tag}] {text}".rstrip())
    return "\n".join(lines)

"""消息文本提取 — 从 LangChain 消息 content 中取纯文本/用户可读文本。

无 textual 依赖，供 WS 服务端（load_history）提取历史消息文本。
"""

from __future__ import annotations

import json

from lumi.agents.core.meta_message import declared_items, injected_prefix


def visible_user_text(msg: object) -> str:
    """用户消息（对象或 dict）的可读文本——所有"这条消息给用户看什么"的单一入口。

    显示声明优先：``lumi.items`` 已声明 → join 各条目 text（``[]`` = 合成消息，
    返回空串）。未声明（cron / 子 agent 等不经 bridge 的构造点，content 本就
    无标签）→ fallback：按 ``injected_prefix`` 计数掉注入前缀块后取文本。
    """
    items = declared_items(msg)
    if items is not None:
        return "\n".join(it.get("text", "") for it in items if it.get("text"))
    if isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = getattr(msg, "content", "")
    skip = injected_prefix(msg)
    if skip and isinstance(content, list):
        content = content[skip:]
    return extract_text_content(content).strip()


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


def _tool_call_desc(tc) -> str:
    """工具调用的 ``name(args)`` 描述。args 经 json.dumps 天然单行（换行被转义）。"""
    name = _tool_call_name(tc)
    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
    if not args:
        return name
    return f"{name}({json.dumps(args, ensure_ascii=False, default=str)})"


def extract_messages_as_text(messages: list) -> str:
    """把消息列表导出成扁平文本，一行一消息，供 dream 语料与 goal 判官转录。

    格式：``[user] …`` / ``[assistant] …`` / ``[assistant→tool:NAME] name({args}) …``
    （带工具调用，参数完整保留——写了哪个文件、跑了什么命令是动作记录的核心）/
    ``[tool:NAME] …``（工具结果）。消息内换行折叠为 ``⏎`` 保证每条恰好一行（grep 友好）；
    system 消息跳过。比 ``messages_to_dict`` 的嵌套 JSON 对窄关键词 grep 友好得多。
    """
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "type", "")
        if role == "system":
            continue
        if role == "human":
            # visible_user_text 对合成 human（摘要 carrier / hook reminder /
            # 后台通知，items 声明为空）返回空串 → 该行天然被丢弃；真实用户
            # 消息取声明文本或 fallback，注入块不会淹没 grep 语料里的真实输入
            raw = visible_user_text(m)
        else:
            raw = extract_text_content(getattr(m, "content", ""))
        text = raw.replace("\n", "⏎").strip()
        if role == "human":
            tag = "user"
        elif role == "ai":
            tool_calls = getattr(m, "tool_calls", None) or []
            if tool_calls:
                names = ",".join(_tool_call_name(tc) for tc in tool_calls)
                tag = f"assistant→tool:{names}"
                calls = " ".join(_tool_call_desc(tc) for tc in tool_calls)
                text = f"{calls} {text}".strip()
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

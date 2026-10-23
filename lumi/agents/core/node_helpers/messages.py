"""消息处理辅助模块

提供消息预处理函数：清理不完整工具调用、卸载大型工具结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from lumi.agents.core.meta_message import INJECTED_PREFIX_KEY, injected_prefix
from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# 共享工具函数（execution / hooks.protocol 等模块复用）
# ---------------------------------------------------------------------------


def content_to_str(content: str | list[Any] | object) -> str:
    """将消息 content 转换为纯文本字符串。多模态 block 转占位避免泄漏 base64。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and "text" in block:
                parts.append(block["text"])
            elif btype == "image":
                source = block.get("source", {})
                mt = (
                    source.get("media_type", "image/?")
                    if isinstance(source, dict)
                    else "image/?"
                )
                parts.append(f"[image: {mt}]")
            elif btype == "image_url":
                parts.append("[image_url]")
            elif btype == "document":
                source = block.get("source", {})
                mt = (
                    source.get("media_type", "application/?")
                    if isinstance(source, dict)
                    else "application/?"
                )
                parts.append(f"[document: {mt}]")
            elif "text" in block:
                # 其他类型(例如 thinking)若带 text 字段仍保留
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def write_offload_file(file_path: Path, content: str) -> None:
    """将内容写入卸载文件（同步，供 ``asyncio.to_thread`` 调用）。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 消息注入
# ---------------------------------------------------------------------------


def inject_text_into_message(message: HumanMessage, text: str) -> HumanMessage:
    """将注入文本作为独立 block 前置到 HumanMessage，返回新消息（不可变原则）。

    同时累加 ``INJECTED_PREFIX_KEY`` 计数——显示侧按计数整块丢弃注入前缀，
    不再正则识别正文。所有向用户消息注入文本的调用点都必须经由本函数。

    空串 content（纯附件消息）不生成空 text 块——空 text 块会随 checkpoint
    永驻历史，Bedrock / 严格 OpenAI 兼容端拒绝空白 text 块导致该会话每轮 400。
    """
    if isinstance(message.content, str):
        content_blocks: list[dict[str, str]] = (
            [{"type": "text", "text": message.content}] if message.content else []
        )
    else:
        content_blocks = list(message.content)

    content_blocks.insert(0, {"type": "text", "text": text})
    return HumanMessage(
        content=content_blocks,
        additional_kwargs={
            **message.additional_kwargs,
            INJECTED_PREFIX_KEY: injected_prefix(message) + 1,
        },
        id=message.id,
    )


def format_reminder(header: str, lines: list[str]) -> str:
    """把一行行列表包成 ``<system-reminder>`` 块（skill / agent 列表注入共用）。"""
    body = "\n".join(lines)
    return f"<system-reminder>\n{header}\n{body}\n</system-reminder>\n"


# ---------------------------------------------------------------------------
# 清理不完整工具调用
# ---------------------------------------------------------------------------


def is_malformed_tool_call(tc: Any) -> bool:
    """缺 id 或 name 的 tool_call（流式聚合残片）——协议层必要字段缺失，配对 / 回传都会炸。"""
    return not tc.get("id") or not tc.get("name")


def dangling_tool_calls(messages: list[Any]) -> list[dict]:
    """messages 里尚未被 ToolMessage 应答的 tool_calls（按 tool_call_id 配对）。"""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [
        tc
        for m in messages
        if isinstance(m, AIMessage)
        for tc in m.tool_calls
        if tc.get("id") not in answered
    ]


def _extract_tool_call_ids(tool_calls: list[Any]) -> set[str | None]:
    """从 tool_calls 中提取所有 id。"""
    ids: set[str | None] = set()
    for tc in tool_calls:
        if isinstance(tc, dict):
            ids.add(tc.get("id"))
        else:
            ids.add(getattr(tc, "id", None))
    return ids


def _has_matching_tool_result(
    messages: list[Any], index: int, tool_call_ids: set[str | None]
) -> bool:
    """检查 ``messages[index]`` 之后是否紧跟匹配的 ToolMessage。"""
    if index + 1 >= len(messages):
        return False
    next_msg = messages[index + 1]
    if not isinstance(next_msg, ToolMessage):
        return False
    return getattr(next_msg, "tool_call_id", None) in tool_call_ids


def _is_dangling_tool_call(messages: list[Any], index: int) -> bool:
    """``messages[index]`` 是否为无结果的 AIMessage(tool_use)——两个清理入口的共用判据。

    遗留的无结果工具调用会导致 Anthropic API 400 错误。
    """
    msg = messages[index]
    if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
        return False
    return not _has_matching_tool_result(
        messages, index, _extract_tool_call_ids(msg.tool_calls)
    )


def drop_incomplete_tool_calls(messages: list[Any]) -> list[Any]:
    """滤掉无结果的 AIMessage(tool_use)，返回可安全发送的投影。

    与 :func:`cleanup_incomplete_tool_calls` 的区别是不改 state、只做一次性投影：
    摘要调用（``run_compact``）走自己的 chain、不经 PreprocessMessages 的清理，而它拿
    到的历史可能带残留 tool_use——上一轮工具执行中途被取消 / 进程被杀后用户又发了新
    消息，落库的 AIMessage(tool_use) 就没有配对结果。
    """
    return [
        msg for i, msg in enumerate(messages) if not _is_dangling_tool_call(messages, i)
    ]


def cleanup_incomplete_tool_calls(messages: list[Any]) -> list[RemoveMessage]:
    """清理没有对应 ToolMessage 的 AIMessage(tool_use)（PreprocessMessages 节点的 state 改写）。"""
    to_remove: list[RemoveMessage] = []
    for i, msg in enumerate(messages):
        if not _is_dangling_tool_call(messages, i):
            continue
        logger.warning(
            "[PreprocessMessages] 发现不完整的工具调用消息 (id: %s), 将其删除以避免 API 错误",
            msg.id,
        )
        to_remove.append(RemoveMessage(id=msg.id))
    return to_remove

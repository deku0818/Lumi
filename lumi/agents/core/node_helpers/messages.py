"""消息处理辅助模块

提供消息预处理函数：清理不完整工具调用、卸载大型工具结果、Anthropic 缓存断点注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from lumi.models.cache import CACHE_CONTROL
from lumi.utils.logger import logger

# ---------------------------------------------------------------------------
# 共享工具函数（供 executor_tools 等模块复用，避免循环导入）
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
    """将文本块插入到 HumanMessage content 最前面，返回新消息（不可变原则）。"""
    if isinstance(message.content, str):
        content_blocks: list[dict[str, str]] = [
            {"type": "text", "text": message.content}
        ]
    else:
        content_blocks = list(message.content)

    content_blocks.insert(0, {"type": "text", "text": text})
    return HumanMessage(
        content=content_blocks,
        additional_kwargs=message.additional_kwargs,
        id=message.id,
    )


def format_reminder(header: str, lines: list[str]) -> str:
    """把一行行列表包成 ``<system-reminder>`` 块（skill / agent 列表注入共用）。"""
    body = "\n".join(lines)
    return f"<system-reminder>\n{header}\n{body}\n</system-reminder>\n"


# ---------------------------------------------------------------------------
# 清理不完整工具调用
# ---------------------------------------------------------------------------


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


def cleanup_incomplete_tool_calls(messages: list[Any]) -> list[RemoveMessage]:
    """清理没有对应 ToolMessage 的 AIMessage(tool_use)。

    遗留的无结果工具调用会导致 Anthropic API 400 错误。
    """
    to_remove: list[RemoveMessage] = []

    for i, msg in enumerate(messages):
        if not isinstance(msg, AIMessage):
            continue
        if not getattr(msg, "tool_calls", None):
            continue

        tool_call_ids = _extract_tool_call_ids(msg.tool_calls)
        if _has_matching_tool_result(messages, i, tool_call_ids):
            continue

        logger.warning(
            "[PreprocessMessages] 发现不完整的工具调用消息 (id: %s), 将其删除以避免 API 错误",
            msg.id,
        )
        to_remove.append(RemoveMessage(id=msg.id))

    return to_remove


# ---------------------------------------------------------------------------
# Anthropic prompt 缓存
# ---------------------------------------------------------------------------


def _add_cache_control(
    msg: HumanMessage | AIMessage | ToolMessage,
) -> HumanMessage | AIMessage | ToolMessage:
    """为消息最后一个内容块添加 ``cache_control``，返回新消息对象。"""
    content = msg.content

    if isinstance(content, str):
        new_content: list[dict[str, Any]] = [
            {"type": "text", "text": content, "cache_control": CACHE_CONTROL}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        last = new_content[-1]
        if isinstance(last, dict):
            new_content[-1] = {**last, "cache_control": CACHE_CONTROL}
        elif isinstance(last, str):
            new_content[-1] = {
                "type": "text",
                "text": last,
                "cache_control": CACHE_CONTROL,
            }
        else:
            return msg
    else:
        return msg

    return msg.model_copy(update={"content": new_content})


def inject_message_cache_breakpoints(messages: list[Any]) -> None:
    """为消息列表末尾添加缓存断点（滑动窗口策略）。

    在倒数第 2 条和最后 1 条非系统消息上添加 ``cache_control``，
    使每轮请求的断点随对话向后滑动。仅对 Anthropic 模型有意义。
    """
    from langchain_core.messages import SystemMessage

    non_system_indices = [
        i for i, m in enumerate(messages) if not isinstance(m, SystemMessage)
    ]
    if not non_system_indices:
        return

    # 倒数第 2 条（如果存在）
    if len(non_system_indices) >= 2:
        idx = non_system_indices[-2]
        messages[idx] = _add_cache_control(messages[idx])

    # 最后 1 条
    idx = non_system_indices[-1]
    messages[idx] = _add_cache_control(messages[idx])

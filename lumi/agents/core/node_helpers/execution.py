"""工具执行辅助模块

提供工具执行相关的辅助函数：截断/卸载过大的工具结果、JSON 提取与修复、错误处理。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from lumi.agents.core.node_helpers.messages import content_to_str, write_offload_file
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import str_token_counter, truncate_docs_to_max_tokens

# 支持 offset/limit 分段读取的工具——截断时附带分段提示而非卸载
_TRUNCATE_ONLY_TOOLS: frozenset[str] = frozenset({"read"})


# ---------------------------------------------------------------------------
# 截断 / 卸载
# ---------------------------------------------------------------------------


def _build_truncation_summary(
    original_text: str,
    truncated_text: str,
    max_tokens: int,
) -> str:
    """构建截断元信息摘要。"""
    orig_tokens = str_token_counter(original_text)
    trunc_tokens = str_token_counter(truncated_text)
    orig_lines = original_text.count("\n") + 1
    trunc_lines = truncated_text.count("\n") + 1

    return (
        f"... [内容已被截断]\n"
        f"已显示：{trunc_tokens} tokens, {trunc_lines} 行\n"
        f"剩余：{orig_tokens - trunc_tokens} tokens, {orig_lines - trunc_lines} 行\n"
        f"原始：{len(original_text)} 字符, {orig_tokens} tokens, {orig_lines} 行\n"
        f"单次工具最大 {max_tokens} tokens"
    )


async def _try_offload_to_file(
    tool_name: str,
    content_str: str,
    max_tokens: int,
) -> str | None:
    """尝试将工具结果卸载到本地文件。成功返回替换文本，失败返回 ``None``。"""
    token_count = str_token_counter(content_str)
    line_count = content_str.count("\n") + 1

    timestamp = datetime.now().strftime("%H%M%S%f")
    file_path = (
        get_config().config_dir / "offload" / f"{tool_name}_result_{timestamp}.txt"
    )

    try:
        await asyncio.to_thread(write_offload_file, file_path, content_str)
    except OSError as exc:
        logger.warning(
            "[truncate_tool_results] 写入文件失败: %s: %s，回退到截断",
            type(exc).__name__,
            exc,
        )
        return None

    logger.info(
        "[truncate_tool_results] %s 结果已卸载到 %s (原始 %d tokens)",
        tool_name,
        file_path,
        token_count,
    )
    return (
        f"工具返回内容过大，已卸载到文件：{file_path}\n"
        f"文件信息：\n"
        f"{len(content_str)} 字符, {token_count} tokens, {line_count} 行\n"
        f"单次工具最大 {max_tokens} tokens\n"
        f"请使用 read 分段读取或 grep 搜索关键内容。"
    )


def _has_multimodal_blocks(content: Any) -> bool:
    """判断 content 是否是包含 image/document block 的 list。"""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") in ("image", "image_url", "document")
        for b in content
    )


async def _truncate_single_message(msg: object, max_tokens: int) -> None:
    """对单条消息执行截断/卸载，就地修改 ``msg.content``。"""
    if not hasattr(msg, "content"):
        return

    original_content = msg.content

    # 多模态 content 不截断:图片已走过压缩管线,再截文本会破坏 block 结构
    if _has_multimodal_blocks(original_content):
        return

    truncated_content = truncate_docs_to_max_tokens(
        original_content, max_tokens=max_tokens
    )
    if truncated_content == original_content:
        return

    content_str = content_to_str(original_content)
    truncated_str = content_to_str(truncated_content)
    tool_name: str = getattr(msg, "name", "unknown")

    # read 等工具：截断并附带分段读取提示
    if tool_name in _TRUNCATE_ONLY_TOOLS:
        summary = _build_truncation_summary(content_str, truncated_str, max_tokens)
        msg.content = (
            f"{truncated_str}\n\n{summary}\n"
            f"可使用 offset 和 limit 参数分段读取剩余内容。"
        )
        return

    # 其他工具：优先卸载到文件，失败则回退截断
    offloaded = await _try_offload_to_file(tool_name, content_str, max_tokens)
    if offloaded:
        msg.content = offloaded
    else:
        summary = _build_truncation_summary(content_str, truncated_str, max_tokens)
        msg.content = f"{truncated_str}\n\n{summary}"


async def truncate_tool_results(messages_list: list[Any]) -> list[Any]:
    """截断或卸载工具返回结果。

    根据工具类型采取不同策略：
    - ``_TRUNCATE_ONLY_TOOLS``：截断并提示分段读取
    - 其他工具：优先卸载到文件，失败时回退到截断
    """
    max_tokens: int = get_config().config.token.once_tool_max_tokens

    for msg in messages_list:
        try:
            await _truncate_single_message(msg, max_tokens)
        except json.JSONDecodeError as exc:
            content_preview = str(msg.content)[:200]
            logger.warning(
                "工具执行完成，但截断失败 (JSONDecodeError: %s). 内容预览: %s",
                exc.msg,
                content_preview,
            )

    return messages_list


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


def handle_tool_error(error: Exception) -> str:
    """处理工具执行异常，返回友好的错误消息。

    被 ``ToolNode`` 内部调用，确保单个工具失败不影响并发执行的其他工具。
    """
    error_message = str(error)
    logger.error("[ToolExecutor] 工具执行失败: %s", error_message)

    if "no search results" in error_message.lower():
        return "此关键词未找到相关搜索结果"

    return f"工具执行失败: {error_message}"

"""工具执行辅助模块

提供工具执行相关的辅助函数：截断/卸载过大的工具结果、错误处理。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from lumi.agents.core.node_helpers.messages import content_to_str, write_offload_file
from lumi.utils.logger import logger
from lumi.utils.paths import lumi_tmp_dir
from lumi.utils.read_config import get_config
from lumi.utils.sizing import (
    content_size,
    text_size,
    truncate_docs_to_max_bytes,
    truncate_text_to_max_bytes,
)

# 支持 offset/limit 分段读取的工具——截断时附带分段提示而非卸载
_TRUNCATE_ONLY_TOOLS: frozenset[str] = frozenset({"read"})

# 卸载替换文本中附带的内容开头预览大小——多数场景模型看预览即可，省一次 read 往返
_OFFLOAD_PREVIEW_BYTES = 2000


# ---------------------------------------------------------------------------
# 截断 / 卸载
# ---------------------------------------------------------------------------


def _build_truncation_summary(
    original_text: str,
    truncated_text: str,
    max_bytes: int,
) -> str:
    """构建截断元信息摘要。"""
    orig_bytes = text_size(original_text)
    trunc_bytes = text_size(truncated_text)
    orig_lines = original_text.count("\n") + 1
    trunc_lines = truncated_text.count("\n") + 1

    return (
        f"... [内容已被截断]\n"
        f"已显示：{trunc_bytes} 字节, {trunc_lines} 行\n"
        f"剩余：{orig_bytes - trunc_bytes} 字节, {orig_lines - trunc_lines} 行\n"
        f"原始：{len(original_text)} 字符, {orig_bytes} 字节, {orig_lines} 行\n"
        f"单次工具最大 {max_bytes} 字节"
    )


async def _try_offload_to_file(
    tool_name: str,
    content_str: str,
    max_bytes: int,
) -> str | None:
    """尝试将工具结果卸载到本地文件。成功返回替换文本，失败返回 ``None``。"""
    byte_count = text_size(content_str)
    line_count = content_str.count("\n") + 1

    timestamp = datetime.now().strftime("%H%M%S%f")
    file_path = lumi_tmp_dir("offload") / f"{tool_name}_result_{timestamp}.txt"

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
        "[truncate_tool_results] %s 结果已卸载到 %s (原始 %d 字节)",
        tool_name,
        file_path,
        byte_count,
    )
    preview = truncate_text_to_max_bytes(content_str, _OFFLOAD_PREVIEW_BYTES)
    if len(preview) < len(content_str) and "\n" in preview:
        # 收到换行边界不留半行；唯一换行在开头（如空行+超长单行）时保留原样
        preview = preview.rsplit("\n", 1)[0] or preview
    return (
        f"工具返回内容过大，已卸载到文件：{file_path}\n"
        f"文件信息：\n"
        f"{len(content_str)} 字符, {byte_count} 字节, {line_count} 行\n"
        f"单次工具最大 {max_bytes} 字节\n"
        f"内容开头预览（前 {text_size(preview)} 字节）：\n"
        f"---\n{preview}\n---\n"
        f"以上仅为开头，完整内容请使用 read 分段读取或 grep 搜索关键内容。"
    )


def _has_multimodal_blocks(content: Any) -> bool:
    """判断 content 是否是包含 image/document block 的 list。"""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") in ("image", "image_url", "document")
        for b in content
    )


async def _truncate_single_message(msg: object, max_bytes: int) -> None:
    """对单条消息执行截断/卸载，就地修改 ``msg.content``。"""
    if not hasattr(msg, "content"):
        return

    original_content = msg.content

    # 多模态 content 不截断:图片已走过压缩管线,再截文本会破坏 block 结构
    if _has_multimodal_blocks(original_content):
        return

    truncated_content = truncate_docs_to_max_bytes(
        original_content, max_bytes=max_bytes
    )
    if truncated_content == original_content:
        return

    content_str = content_to_str(original_content)
    truncated_str = content_to_str(truncated_content)
    tool_name: str = getattr(msg, "name", "unknown")

    # read 等工具：截断并附带分段读取提示
    if tool_name in _TRUNCATE_ONLY_TOOLS:
        summary = _build_truncation_summary(content_str, truncated_str, max_bytes)
        msg.content = (
            f"{truncated_str}\n\n{summary}\n"
            f"可使用 offset 和 limit 参数分段读取剩余内容。"
        )
        return

    # 其他工具：优先卸载到文件，失败则回退截断
    offloaded = await _try_offload_to_file(tool_name, content_str, max_bytes)
    if offloaded:
        msg.content = offloaded
    else:
        summary = _build_truncation_summary(content_str, truncated_str, max_bytes)
        msg.content = f"{truncated_str}\n\n{summary}"


# 聚合超预算时公平份额的下限——必须大于卸载替换文本（头部说明 +
# _OFFLOAD_PREVIEW_BYTES 预览 ≈ 2.4KB），保证「收缩」恒真收缩不反弹
_MIN_PER_MSG_CAP = 4096
assert _MIN_PER_MSG_CAP > _OFFLOAD_PREVIEW_BYTES  # 见上：份额必须容得下预览


async def truncate_tool_results(messages_list: list[Any]) -> list[Any]:
    """截断或卸载工具返回结果。

    根据工具类型采取不同策略：
    - ``_TRUNCATE_ONLY_TOOLS``：截断并提示分段读取
    - 其他工具：优先卸载到文件，失败时回退到截断

    单条上限 ``once_tool_max_bytes``；本轮合计超 ``round_tool_max_bytes`` 时收紧为
    公平份额（budget // N，下限 ``_MIN_PER_MSG_CAP``）。每条消息只处理一次——截断
    元信息恒描述工具真实原始输出，也不会出现指针文本被再次卸载。
    """
    token_config = get_config().config.token
    max_bytes: int = token_config.once_tool_max_bytes

    # 多模态与无 content 的消息天然是 no-op，只收集可截断候选并复用其字节数
    candidates = [
        m
        for m in messages_list
        if hasattr(m, "content") and not _has_multimodal_blocks(m.content)
    ]
    sizes = [content_size(m.content) for m in candidates]
    budget = token_config.round_tool_max_bytes
    if sum(sizes) > budget:
        max_bytes = min(max_bytes, max(budget // len(candidates), _MIN_PER_MSG_CAP))

    # 只处理超上限的候选——under-cap 的截断本就是 no-op，跳过省一次全量 encode
    for msg, size in zip(candidates, sizes):
        if size > max_bytes:
            await _truncate_single_message(msg, max_bytes)

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

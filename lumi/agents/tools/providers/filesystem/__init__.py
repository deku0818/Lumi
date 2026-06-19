"""Filesystem工具提供者 - 提供本地文件系统操作工具

该模块提供文件读取、写入、编辑、列目录、glob查找和grep搜索功能。
所有文件操作都在授权目录范围内执行，通过路径校验确保安全。

本包作为公共门面，re-export 后端（backend）、ripgrep 解析（ripgrep）与
工具函数（tools）。read 工具因涉及多模态 dispatch 与测试 monkeypatch
（read_image_with_token_budget 在本模块命名空间被替换），留在此处。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import fitz
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import Field

from lumi.agents.core.meta_message import meta_human_message
from lumi.agents.tools.providers.filesystem.backend import (
    BINARY_CHECK_BYTES,
    DEFAULT_CONTENT_HEAD_LIMIT,
    DEFAULT_READ_LIMIT,
    RIPGREP_TIMEOUT_SECONDS,
    LocalFilesystemBackend,
    check_empty_content,
    format_content_with_line_numbers,
    get_backend,
    perform_string_replacement,
)
from lumi.agents.tools.providers.filesystem.media import (
    PDF_INLINE_PAGE_THRESHOLD,
    PDF_MAX_EXTRACT_SIZE,
    PDF_MAX_PAGES_PER_READ,
    SUPPORTED_IMAGE_EXTS,
    SUPPORTED_PDF_EXTS,
    MediaReadError,
    extract_pdf_pages,
    parse_pages_param,
    read_image_with_token_budget,
    validate_pdf_bytes,
)
from lumi.agents.tools.providers.filesystem.tools import (
    EditInput,
    GlobInput,
    GrepInput,
    WriteInput,
    edit,
    glob,
    grep,
    write,
)
from lumi.utils.logger import logger

# ============================================================================
# Tool Functions
# ============================================================================


@tool
async def read(
    file_path: Annotated[
        str, Field(description="文件路径,如 config.json 或 src/main.py")
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
    offset: Annotated[int, Field(description="起始行号(从0开始,仅对文本文件有效)")] = 0,
    limit: Annotated[int, Field(description="最大读取行数(仅对文本文件有效)")] = 200,
    pages: Annotated[
        str | None,
        Field(
            description=(
                "PDF 页码范围,仅对 .pdf 文件有效。"
                "格式: '1-5' / '1,3,5' / '1-3,7,9-10'。"
                "单次最多读取 20 页。不传时小 PDF(≤10 页)整体渲染为图片,"
                "大 PDF 必须传此参数。"
            )
        ),
    ] = None,
) -> str | Command:
    """读取文件内容。文本文件返回带行号的文本;
    图片(PNG/JPG/GIF/WebP)作为 image block 注入对话;
    PDF 渲染为图片页(小 PDF 整体;大 PDF 按 pages 分段)。"""
    resolved = Path(file_path).expanduser().resolve()
    if not resolved.exists():
        return f"错误: 文件 '{file_path}' 不存在"

    ext = resolved.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTS:
        return await _read_image_command(resolved, tool_call_id)
    if ext in SUPPORTED_PDF_EXTS:
        return await _read_pdf_command(resolved, pages, tool_call_id)

    # 文本路径沿用 backend(前面多了一次 exists/扩展名判断)
    backend = get_backend()
    return await backend.read(file_path, offset, limit)


# ============================================================================
# 多模态 dispatch 辅助函数 (read 工具使用)
# ============================================================================


def _error_command(tool_call_id: str, message: str, hint: str | None = None) -> Command:
    """构造只含 ToolMessage 的错误返回。

    status="error" 让 TUI widget renderer / 统计层能与成功结果分开计数并
    以错误样式渲染。
    """
    content = f"错误: {message}"
    if hint:
        content += f"\n提示: {hint}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="read",
                    status="error",
                )
            ]
        }
    )


async def _read_image_command(path: Path, tool_call_id: str) -> Command:
    """图片分支: ToolMessage 文本摘要 + HumanMessage 携带 image block。"""
    try:
        img = await read_image_with_token_budget(path)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[_read_image_command] 处理图片 %s 失败: %s: %s",
            path.name,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return _error_command(
            tool_call_id,
            f"处理图片 {path.name} 失败: {e}",
            "请确认文件是有效的 PNG/JPG/GIF/WebP",
        )

    summary = (
        f"已读取图片 {path.name}: {img.width}x{img.height}, "
        f"{img.media_type}, 原始 {img.original_size} 字节"
    )
    reminder = (
        f"<system-reminder>read 工具读取的图片 {path.name} 内容如下:</system-reminder>"
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=summary,
                    tool_call_id=tool_call_id,
                    name="read",
                ),
                meta_human_message(
                    [
                        {"type": "text", "text": reminder},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.media_type,
                                "data": img.base64_data,
                            },
                        },
                    ]
                ),
            ]
        }
    )


def _inspect_pdf(path: Path) -> tuple[bytes, int]:
    """读字节 + magic 校验 + 总页数。raw 返回供下游复用避免重复读盘。"""
    raw = validate_pdf_bytes(path, PDF_MAX_EXTRACT_SIZE)
    with fitz.open(stream=raw, filetype="pdf") as doc:
        page_count = doc.page_count
    return raw, page_count


async def _read_pdf_command(
    path: Path,
    pages: str | None,
    tool_call_id: str,
) -> Command:
    """PDF 分支:不传 pages 渲染全部页(≤10);传了 pages 渲染指定页。"""
    # 同步 IO 挪到 worker 线程,raw 供下游复用
    try:
        raw, total_pages = await asyncio.to_thread(_inspect_pdf, path)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        return _error_command(
            tool_call_id,
            f"无法解析 PDF {path.name}: {e}",
            "请确认文件未损坏且非加密 PDF",
        )

    if pages is not None and pages.strip():
        try:
            indices = parse_pages_param(pages, total_pages)
        except MediaReadError as e:
            return _error_command(tool_call_id, str(e), e.hint)
        return await _read_pdf_rendered_command(path, indices, tool_call_id, raw=raw)

    if total_pages > PDF_INLINE_PAGE_THRESHOLD:
        return _error_command(
            tool_call_id,
            f"PDF 共 {total_pages} 页,超过整体读取阈值 {PDF_INLINE_PAGE_THRESHOLD} 页",
            f"请使用 pages 参数分页读取,例如 pages='1-{min(total_pages, PDF_MAX_PAGES_PER_READ)}'",
        )

    return await _read_pdf_rendered_command(
        path, list(range(total_pages)), tool_call_id, raw=raw
    )


async def _read_pdf_rendered_command(
    path: Path,
    page_indices: list[int],
    tool_call_id: str,
    raw: bytes | None = None,
) -> Command:
    """PDF 渲染路径:把指定页作为 image blocks 注入。

    raw 由 _read_pdf_command 的 _inspect_pdf 复用传入,避免 extract_pdf_pages
    重新读盘/重走 magic 校验。
    """
    try:
        rendered = await extract_pdf_pages(path, page_indices, raw=raw)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[_read_pdf_rendered_command] 渲染 PDF %s 失败: %s: %s",
            path.name,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return _error_command(
            tool_call_id,
            f"渲染 PDF {path.name} 失败: {e}",
            "请确认文件未损坏",
        )

    # zip 对不等长会静默截断,断言页码不会错位
    assert len(rendered) == len(page_indices), (
        f"extract_pdf_pages 返回 {len(rendered)} 张,期望 {len(page_indices)} 张"
    )
    page_numbers = ",".join(str(i + 1) for i in page_indices)
    reminder = (
        f"<system-reminder>read 工具渲染了 PDF {path.name} 的第 "
        f"{page_numbers} 页:</system-reminder>"
    )
    blocks: list[dict] = [{"type": "text", "text": reminder}]
    for idx, img in zip(page_indices, rendered):
        blocks.append({"type": "text", "text": f"--- 第 {idx + 1} 页 ---"})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": img.base64_data,
                },
            }
        )

    summary = f"已渲染 PDF {path.name} 第 {len(rendered)} 页: {page_numbers}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=summary,
                    tool_call_id=tool_call_id,
                    name="read",
                ),
                meta_human_message(blocks),
            ]
        }
    )


__all__ = [
    "BINARY_CHECK_BYTES",
    "DEFAULT_CONTENT_HEAD_LIMIT",
    "DEFAULT_READ_LIMIT",
    "RIPGREP_TIMEOUT_SECONDS",
    "EditInput",
    "GlobInput",
    "GrepInput",
    "LocalFilesystemBackend",
    "WriteInput",
    "check_empty_content",
    "edit",
    "format_content_with_line_numbers",
    "get_backend",
    "glob",
    "grep",
    "perform_string_replacement",
    "read",
    "read_image_with_token_budget",
    "write",
]

"""Vision 工具 — 让无视觉能力的主模型识别图片 / PDF。

主模型不具备多模态时，在 config.yaml 的 ``vision`` 配置一个视觉辅助模型（model +
可选 base_url/api_key，连接留空则反查 providers.json 里含该模型的 profile；见
provider_store.resolve_vision）。本工具把图片 / PDF（本地路径或 http(s) URL）连同
主模型的**具体问题**交给该模型识别，返回文字答案 —— 主模型据此作答、亦可反复追问。

仅在 config.yaml 配了 ``vision.model`` 时注册（get_vision_tools 条件加载；未配则不出现）。
本模块可安全使用 `from __future__ import annotations`：无 ToolRuntime 注入参数。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import fitz
import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import Field

from lumi.agents.tools.providers.filesystem.media import (
    PDF_INLINE_PAGE_THRESHOLD,
    PDF_MAGIC,
    PDF_MAX_EXTRACT_SIZE,
    SUPPORTED_IMAGE_EXTS,
    SUPPORTED_PDF_EXTS,
    MediaReadError,
    compress_image_bytes,
    extract_pdf_pages,
    read_image_with_token_budget,
    validate_pdf_bytes,
)
from lumi.utils.logger import logger

_DOWNLOAD_TIMEOUT = 30.0
_MAX_DOWNLOAD_BYTES = PDF_MAX_EXTRACT_SIZE  # 与 PDF 上限一致，防超大文件耗尽内存


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _image_block(media_type: str, b64: str) -> dict:
    """Anthropic canonical image block；message_transform 再按视觉模型 provider 转格式。"""
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": b64},
    }


async def _download(url: str) -> bytes:
    """流式下载并累计大小，超上限即断——不把整个 body 读进内存后才判。"""
    limit_mb = _MAX_DOWNLOAD_BYTES // 1024 // 1024
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            # Content-Length 已知且超限 → 未下载即拒
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > _MAX_DOWNLOAD_BYTES:
                raise MediaReadError(f"下载内容超过上限 {limit_mb}MB")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise MediaReadError(f"下载内容超过上限 {limit_mb}MB")
                chunks.append(chunk)
    return b"".join(chunks)


async def _pdf_blocks_from_raw(raw: bytes, label: str) -> list[dict]:
    """PDF 原始字节 → 图片块（渲染前若干页，走图片压缩管线）。"""

    def _count() -> int:
        with fitz.open(stream=raw, filetype="pdf") as doc:
            return doc.page_count

    total = await asyncio.to_thread(_count)
    n = min(total, PDF_INLINE_PAGE_THRESHOLD)
    imgs = await extract_pdf_pages(Path(label), list(range(n)), raw=raw)
    blocks: list[dict] = []
    if total > n:
        blocks.append(
            {"type": "text", "text": f"（{label} 共 {total} 页，仅识别前 {n} 页）"}
        )
    for i, img in enumerate(imgs):
        blocks.append({"type": "text", "text": f"--- 第 {i + 1} 页 ---"})
        blocks.append(_image_block(img.media_type, img.base64_data))
    return blocks


async def _image_blocks_from_raw(raw: bytes) -> list[dict]:
    """URL 下载的图片字节 → 单个图片块（复用 media 的字节级两阶段压缩管线）。"""
    img = await asyncio.to_thread(compress_image_bytes, raw)
    return [_image_block(img.media_type, img.base64_data)]


async def _load_blocks(file_path: str) -> list[dict]:
    """把本地路径 / http(s) URL 的图片或 PDF 载入为 Anthropic image blocks。

    URL 按内容嗅探（%PDF- magic → PDF，否则按图片）；本地按扩展名分流。
    """
    if _is_url(file_path):
        raw = await _download(file_path)
        if raw.startswith(PDF_MAGIC):
            return await _pdf_blocks_from_raw(raw, file_path)
        return await _image_blocks_from_raw(raw)

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise MediaReadError(f"文件不存在: {file_path}")
    ext = path.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTS:
        img = await read_image_with_token_budget(path)
        return [_image_block(img.media_type, img.base64_data)]
    if ext in SUPPORTED_PDF_EXTS:
        raw = await asyncio.to_thread(validate_pdf_bytes, path, PDF_MAX_EXTRACT_SIZE)
        return await _pdf_blocks_from_raw(raw, path.name)
    raise MediaReadError(f"不支持的文件类型 {ext}，仅支持图片(PNG/JPG/GIF/WebP)与 PDF")


@tool
async def vision(
    file_path: Annotated[
        str, Field(description="图片或 PDF 的本地路径，或 http(s) URL")
    ],
    question: Annotated[
        str,
        Field(
            description="希望从图片/PDF 中了解的具体问题，如「图里的总金额是多少」「这张截图报了什么错」"
        ),
    ],
) -> str:
    """识别图片或 PDF 的内容并回答关于它的具体问题。

    当前主模型不具备图片识别能力；需要理解图片/PDF（截图、照片、扫描件、图表、表格等）时
    调用本工具。传入文件的本地路径或 http(s) URL，以及你想了解的具体问题，工具会用视觉辅助
    模型给出文字答案。同一文件可带不同问题多次调用以获取更细节的信息。
    """
    from lumi.agents.core.response import extract_ainvoke_content, message_transform
    from lumi.models import provider_store
    from lumi.models.manager import create_llm

    resolved = provider_store.resolve_vision()
    if resolved is None:
        return "错误: 未配置视觉辅助模型。请在 config.yaml 的 vision.model 中配置一个具备视觉能力的模型（重启生效）。"

    try:
        blocks = await _load_blocks(file_path)
    except MediaReadError as e:
        return f"错误: {e}"
    except httpx.HTTPError as e:
        return f"错误: 下载失败 {file_path}: {e}"
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[vision] 载入 %s 失败: %s: %s",
            file_path,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return f"错误: 无法读取 {file_path}: {e}"

    content = await message_transform(
        [{"type": "text", "text": question}, *blocks], model_name=resolved.model
    )
    llm_params: dict = {}
    if resolved.base_url:
        llm_params["base_url"] = resolved.base_url
    if resolved.api_key:
        llm_params["api_key"] = resolved.api_key
    try:
        llm = create_llm(resolved.model, **llm_params)
        resp = await llm.ainvoke([HumanMessage(content=content)])
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[vision] 视觉模型 %s 调用失败: %s: %s",
            resolved.model,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return f"错误: 视觉模型 {resolved.model} 识别失败: {e}"
    return extract_ainvoke_content(resp.content) or "（视觉模型未返回可读文本）"


async def get_vision_tools(names: list[str] | None = None) -> list:
    """条件加载：config.yaml 配置了视觉辅助模型（``vision.model``）时才提供 vision 工具。

    async loader 每次 get_tools 调用都重新求值；registry 不对 loader 结果按 names 后置过滤，
    故此处自行处理白名单。config.yaml 改动需重启 sidecar 生效。
    """
    from lumi.utils.read_config import get_config

    if not get_config().config.vision.model:
        return []
    if names is not None and "vision" not in names:
        return []
    return [vision]

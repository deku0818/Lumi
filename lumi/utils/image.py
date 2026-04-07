"""图片下载与 Base64 编码工具

用于将 URL 图片下载并转换为 base64 编码，主要服务于
AWS Bedrock 模型不支持 URL 图片源的场景。
"""

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from lumi.utils.constants import IMAGE_FETCH_TIMEOUT, MAX_IMAGE_SIZE
from lumi.utils.logger import logger

# 支持的图片 MIME 类型
SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)

# 扩展名到 MIME 类型的映射
_EXT_TO_MEDIA_TYPE: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class ImageData:
    """下载后的图片数据"""

    media_type: str
    data: str  # base64 编码字符串


def _detect_media_type(url: str, content_type: str | None) -> str:
    """从 HTTP Content-Type 或 URL 扩展名推断 MIME 类型

    优先使用 Content-Type header，回退到 URL 路径扩展名。
    """
    # 优先从 Content-Type 推断
    if content_type:
        # 取分号前的部分，如 "image/jpeg; charset=utf-8" -> "image/jpeg"
        mime = content_type.split(";")[0].strip().lower()
        if mime in SUPPORTED_MEDIA_TYPES:
            return mime

    # 回退到 URL 扩展名
    path = urlparse(url).path.lower()
    for ext, mime in _EXT_TO_MEDIA_TYPE.items():
        if path.endswith(ext):
            return mime

    # 无法确定类型，默认 JPEG
    logger.warning(
        f"无法确定图片 MIME 类型 (Content-Type: {content_type})，默认使用 image/jpeg: {url}"
    )
    return "image/jpeg"


async def download_image_as_base64(
    url: str, timeout: float = IMAGE_FETCH_TIMEOUT
) -> ImageData:
    """异步下载图片并返回 base64 编码数据

    会先检查 Content-Length header，再流式下载并累计检查大小，
    避免超大文件耗尽内存。

    Args:
        url: 图片 URL
        timeout: 请求超时时间（秒）

    Returns:
        ImageData: 包含 media_type 和 base64 data 的数据对象

    Raises:
        httpx.HTTPStatusError: HTTP 请求失败
        ValueError: 图片过大
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async with client.stream("GET", url, timeout=timeout) as response:
            response.raise_for_status()

            # 先检查 Content-Length header（如果存在）
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_IMAGE_SIZE:
                raise ValueError(
                    f"图片 Content-Length {content_length} 字节超过限制 {MAX_IMAGE_SIZE} 字节: {url}"
                )

            # 流式下载并累计检查大小
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_SIZE:
                    raise ValueError(f"图片大小超过限制 {MAX_IMAGE_SIZE} 字节: {url}")
                chunks.append(chunk)

            content = b"".join(chunks)
            content_type = response.headers.get("content-type")

        media_type = _detect_media_type(url, content_type)
        data = base64.b64encode(content).decode("ascii")

        logger.debug(f"图片下载成功: {url} ({media_type}, {len(content)} bytes)")
        return ImageData(media_type=media_type, data=data)

"""剪贴板图片读取工具

从系统剪贴板读取图片数据，返回 ImageData。
通过 Pillow ImageGrab 支持 macOS / Windows / Linux(X11)。
"""

import asyncio
import base64
import io

from lumi.utils.image import ImageData
from lumi.utils.logger import logger


def _read_clipboard() -> ImageData | None:
    """通过 Pillow 读取剪贴板中的图片，转为 PNG base64。"""
    try:
        from PIL import ImageGrab

        img = ImageGrab.grabclipboard()
        if img is None:
            return None

        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=0)
        raw = buf.getvalue()
        data = base64.b64encode(raw).decode("ascii")
        logger.debug(f"[clipboard] 读取到剪贴板图片: {len(raw)} bytes")
        return ImageData(media_type="image/png", data=data)
    except Exception:
        logger.warning("[clipboard] 读取剪贴板图片失败", exc_info=True)
        return None


async def read_image_from_clipboard() -> ImageData | None:
    """异步读取系统剪贴板中的图片。

    Returns:
        ImageData: 图片数据，无图片时返回 None。
    """
    return await asyncio.to_thread(_read_clipboard)

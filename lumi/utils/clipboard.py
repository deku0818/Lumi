"""剪贴板图片读取工具

从系统剪贴板读取图片数据，返回 ImageData。
通过 Pillow ImageGrab 支持 macOS / Windows / Linux(X11)。
"""

import asyncio
import base64
import functools
import io
import platform
import shutil
import subprocess

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


@functools.cache
def _get_copy_command() -> tuple[str, ...] | None:
    """根据平台返回剪贴板写入命令，不支持时返回 None。

    结果会被缓存，因为平台和可用工具在进程生命周期内不会变化。
    """
    system = platform.system()
    match system:
        case "Darwin":
            return ("pbcopy",)
        case "Linux":
            if shutil.which("xclip"):
                return ("xclip", "-selection", "clipboard")
            if shutil.which("xsel"):
                return ("xsel", "--clipboard", "--input")
            return None
        case "Windows":
            return ("clip.exe",)
        case _:
            return None


def copy_to_clipboard(text: str) -> bool:
    """将文本写入系统剪贴板。

    支持 macOS（pbcopy）、Linux（xclip/xsel）、Windows（clip.exe）。

    Args:
        text: 要复制的文本内容。

    Returns:
        是否成功写入剪贴板。
    """
    cmd = _get_copy_command()
    if cmd is None:
        logger.warning("[clipboard] 当前平台无可用的剪贴板写入工具")
        return False
    try:
        subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            check=True,
            timeout=3,
        )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ):
        logger.warning("[clipboard] 写入剪贴板失败", exc_info=True)
        return False

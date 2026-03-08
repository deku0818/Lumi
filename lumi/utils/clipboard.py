"""剪贴板图片读取工具

从系统剪贴板读取图片数据，返回 ImageData。
目前仅支持 macOS（通过 osascript 读取剪贴板 PNG 数据）。
"""

import asyncio
import base64
import subprocess
import tempfile
from pathlib import Path

from lumi.utils.image import ImageData
from lumi.utils.logger import logger

# macOS osascript：将剪贴板 PNG 数据写入指定文件路径（路径通过 argv 传入）
_APPLESCRIPT_READ = """on run argv
set theFile to POSIX file (item 1 of argv)
set pngData to the clipboard as «class PNGf»
set fd to open for access theFile with write permission
write pngData to fd
close access fd
end run"""


def _read_clipboard_macos() -> ImageData | None:
    """macOS: 通过 osascript 读取剪贴板中的 PNG 图片。"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["osascript", "-e", _APPLESCRIPT_READ, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not tmp_path.exists():
            return None

        raw = tmp_path.read_bytes()
        if not raw:
            return None

        data = base64.b64encode(raw).decode("ascii")
        logger.debug(f"[clipboard] 读取到剪贴板图片: {len(raw)} bytes")
        return ImageData(media_type="image/png", data=data)
    except subprocess.TimeoutExpired:
        logger.warning("[clipboard] osascript 执行超时")
        return None
    except Exception:
        logger.warning("[clipboard] 读取剪贴板图片失败", exc_info=True)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


async def read_image_from_clipboard() -> ImageData | None:
    """异步读取系统剪贴板中的图片。

    Returns:
        ImageData: 图片数据，无图片时返回 None。
    """
    return await asyncio.to_thread(_read_clipboard_macos)

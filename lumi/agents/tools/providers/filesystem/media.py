"""图片 / PDF 处理辅助模块

对 read 工具提供读取图片（PNG/JPG/GIF/WebP）和 PDF 的能力。核心设计:

- 图片:两阶段压缩管线
  1. maybe_resize_and_downsample_image: 满足 API 硬约束(5MB base64, 2000x2000 px)
  2. compress_image_with_token_budget: 满足对话 token 预算(默认 25k)

- PDF:统一渲染为图片(每页走图片管线),避免 provider 间的 document block 差异

所有处理全程在内存中完成,不落临时文件。
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
from PIL import Image, ImageOps, UnidentifiedImageError

from lumi.utils.logger import logger

# ─────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────

# ── 图片 ──
IMAGE_API_MAX_BASE64_SIZE = 5 * 1024 * 1024
"""Anthropic API 接受的 base64 图片最大字节数"""

IMAGE_TARGET_RAW_SIZE = int(5 * 1024 * 1024 * 3 / 4)  # 3.75 MB
"""目标原始字节数 (base64 膨胀 4/3 后仍在 API 上限内)"""

IMAGE_MAX_DIMENSION = 2000
"""图片最大边长 (客户端限制,API 会服务端再缩到 1568)"""

IMAGE_FALLBACK_DIMENSION = 1000
"""兜底尺寸:当标准 resize 仍超限时使用"""

DEFAULT_IMAGE_MAX_TOKENS = 25000
"""单张图片的默认 token 预算"""

TOKEN_PER_BASE64_CHAR = 0.125
"""经验公式:每 8 个 base64 字符约 1 token"""

# ── PDF ──
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024
"""PDF 文件大小上限(防止资源耗尽)"""

PDF_MAX_PAGES_PER_READ = 20
"""单次 Read 调用 pages 参数的最大页数跨度"""

PDF_INLINE_PAGE_THRESHOLD = 10
"""不传 pages 时允许整体读取的最大页数"""

PDF_MAGIC = b"%PDF-"
"""PDF 文件 magic bytes (防止被伪装的非 PDF 文件毒死 session)"""

PDF_RENDER_DPI = 150
"""PyMuPDF get_pixmap 渲染 DPI;150 dpi 下 A4 约 1240x1754 px,
在可读性(小字/表格)与压缩后图片大小之间的经验取值。"""

# ── 扩展名集合 ──
SUPPORTED_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
SUPPORTED_PDF_EXTS = frozenset({".pdf"})


# ─────────────────────────────────────────────────────────────────────────
# 错误类型
# ─────────────────────────────────────────────────────────────────────────


class MediaReadError(Exception):
    """带可执行提示的媒体读取错误。

    hint 字段给出用户/模型可以采取的具体修复动作,
    例如"请使用 pages 参数分页读取: pages='1-10'"。
    """

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


# ─────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────


ImageMediaType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
"""允许的图片 media type 集合(与 Anthropic/OpenAI API 都兼容)。"""


@dataclass(frozen=True)
class CompressedImage:
    """压缩管线输出的图片结果。base64 按需计算,避免和 data 同时驻留内存。"""

    data: bytes
    media_type: ImageMediaType
    width: int
    height: int
    original_size: int

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("CompressedImage.data 不能为空")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"CompressedImage 尺寸必须为正: width={self.width}, height={self.height}"
            )
        if self.original_size <= 0:
            raise ValueError(
                f"CompressedImage.original_size 必须为正: {self.original_size}"
            )

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


# ─────────────────────────────────────────────────────────────────────────
# 图片格式嗅探 (magic bytes,不信扩展名)
# ─────────────────────────────────────────────────────────────────────────


def detect_image_format(buf: bytes) -> str:
    """根据 magic bytes 识别图片 media type。

    未知格式返回 "image/png" 作为保守默认值 (PIL 能兜底)。
    """
    if len(buf) < 4:
        return "image/png"

    # PNG: 89 50 4E 47
    if buf[0] == 0x89 and buf[1:4] == b"PNG":
        return "image/png"

    # JPEG: FF D8 FF
    if buf[0] == 0xFF and buf[1] == 0xD8 and buf[2] == 0xFF:
        return "image/jpeg"

    # GIF: "GIF87a" / "GIF89a"
    if buf[:3] == b"GIF":
        return "image/gif"

    # WebP: "RIFF" .... "WEBP"
    if len(buf) >= 12 and buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"

    return "image/png"


# ─────────────────────────────────────────────────────────────────────────
# 图片压缩管线 - 阶段 1: 满足 API 硬约束
# ─────────────────────────────────────────────────────────────────────────


def _encode_image(img: Image.Image, fmt: str, **save_kwargs) -> bytes:
    """把 PIL Image 编码为指定格式的字节。RGBA -> JPEG 时加白色背景。"""
    if fmt.upper() == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        # JPEG 不支持透明通道,用白色背景合成
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            background.paste(img, mask=img.split()[-1])
        else:
            background.paste(img)
        img = background

    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def _build_compressed(
    data: bytes,
    media_type: ImageMediaType,
    width: int,
    height: int,
    original_size: int,
) -> CompressedImage:
    return CompressedImage(
        data=data,
        media_type=media_type,
        width=width,
        height=height,
        original_size=original_size,
    )


def _try_jpeg_qualities(
    img: Image.Image,
    target_bytes: int,
    qualities: tuple[int, ...] = (80, 60, 40, 20),
) -> bytes | None:
    """按 quality 降级尝试 JPEG 编码,首个 <= target_bytes 的结果返回。"""
    for q in qualities:
        encoded = _encode_image(img, "JPEG", quality=q)
        if len(encoded) <= target_bytes:
            return encoded
    return None


def maybe_resize_and_downsample_image(
    raw: bytes,
    media_type_hint: str | None = None,
) -> CompressedImage:
    """图片压缩管线 - 阶段 1:满足 API 硬约束 (5MB base64 / 2000x2000)。

    策略:
    1. 快路径:原始 <=3.75MB 且 <=2000x2000 直通
    2. 先保分辨率压缩: PNG palette / JPEG 80/60/40/20
    3. 再 resize 到 2000x2000 边界,重试压缩
    4. 兜底: 1000x1000 + JPEG q20
    """
    if not raw:
        raise MediaReadError(
            "图片文件为空",
            hint="请检查文件是否完整",
        )

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise MediaReadError(
            f"无法解析图片: {e}",
            hint="请确认文件是有效的 PNG/JPG/GIF/WebP",
        ) from e

    # EXIF 旋转失败会让模型看到方向错误的图片,影响输出质量
    try:
        img = ImageOps.exif_transpose(img)
    except (OSError, ValueError, AttributeError) as e:
        logger.warning("EXIF 旋转失败, 以原方向继续: %s: %s", type(e).__name__, e)

    original_size = len(raw)
    media_type = media_type_hint if media_type_hint else detect_image_format(raw)
    # Pillow format names 映射到 media type 格式
    fmt = media_type.split("/")[-1].upper()
    if fmt == "JPG":
        fmt = "JPEG"

    width, height = img.size

    # ── 快路径:满足所有约束直接返回 ──
    if (
        original_size <= IMAGE_TARGET_RAW_SIZE
        and width <= IMAGE_MAX_DIMENSION
        and height <= IMAGE_MAX_DIMENSION
    ):
        return _build_compressed(raw, media_type, width, height, original_size)

    # ── 先尝试保分辨率压缩 ──
    needs_resize = width > IMAGE_MAX_DIMENSION or height > IMAGE_MAX_DIMENSION

    if not needs_resize:
        # 仅字节数超限,尝试压缩
        if fmt == "PNG":
            try:
                png_palette = _encode_image(img, "PNG", optimize=True)
                if len(png_palette) <= IMAGE_TARGET_RAW_SIZE:
                    return _build_compressed(
                        png_palette, "image/png", width, height, original_size
                    )
                # 再试 palette 模式
                palette_img = img.convert("P", palette=Image.Palette.ADAPTIVE)
                png_palette2 = _encode_image(palette_img, "PNG", optimize=True)
                if len(png_palette2) <= IMAGE_TARGET_RAW_SIZE:
                    return _build_compressed(
                        png_palette2,
                        "image/png",
                        width,
                        height,
                        original_size,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[maybe_resize_and_downsample_image] PNG 保分辨率压缩失败,降级到 JPEG: %s: %s",
                    type(e).__name__,
                    e,
                )

        jpeg_data = _try_jpeg_qualities(img, IMAGE_TARGET_RAW_SIZE)
        if jpeg_data is not None:
            return _build_compressed(
                jpeg_data, "image/jpeg", width, height, original_size
            )
        # 落到 resize 流程

    # ── 计算 resize 后的尺寸,保持宽高比 ──
    new_width, new_height = width, height
    if new_width > IMAGE_MAX_DIMENSION:
        new_height = round(new_height * IMAGE_MAX_DIMENSION / new_width)
        new_width = IMAGE_MAX_DIMENSION
    if new_height > IMAGE_MAX_DIMENSION:
        new_width = round(new_width * IMAGE_MAX_DIMENSION / new_height)
        new_height = IMAGE_MAX_DIMENSION

    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # resize 后再尝试压缩
    if fmt == "PNG":
        try:
            png_data = _encode_image(resized, "PNG", optimize=True)
            if len(png_data) <= IMAGE_TARGET_RAW_SIZE:
                return _build_compressed(
                    png_data,
                    "image/png",
                    new_width,
                    new_height,
                    original_size,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[maybe_resize_and_downsample_image] resize 后 PNG 压缩失败,降级到 JPEG: %s: %s",
                type(e).__name__,
                e,
            )

    jpeg_data = _try_jpeg_qualities(resized, IMAGE_TARGET_RAW_SIZE)
    if jpeg_data is not None:
        return _build_compressed(
            jpeg_data, "image/jpeg", new_width, new_height, original_size
        )

    # ── 兜底: 1000x1000 + JPEG q20 ──
    fallback_size = min(new_width, new_height, IMAGE_FALLBACK_DIMENSION)
    if new_width >= new_height:
        fb_w = fallback_size
        fb_h = round(new_height * fallback_size / new_width)
    else:
        fb_h = fallback_size
        fb_w = round(new_width * fallback_size / new_height)
    fb_w = max(fb_w, 1)
    fb_h = max(fb_h, 1)

    fallback_img = img.resize((fb_w, fb_h), Image.Resampling.LANCZOS)
    fallback_data = _encode_image(fallback_img, "JPEG", quality=20)
    return _build_compressed(fallback_data, "image/jpeg", fb_w, fb_h, original_size)


# ─────────────────────────────────────────────────────────────────────────
# 图片压缩管线 - 阶段 2: 满足 token 预算
# ─────────────────────────────────────────────────────────────────────────


def compress_image_with_token_budget(
    img: CompressedImage,
    max_tokens: int = DEFAULT_IMAGE_MAX_TOKENS,
) -> CompressedImage:
    """图片压缩管线 - 阶段 2:满足 token 预算。

    token 换算公式(经验):
        tokens ≈ base64_chars * 0.125
        target_raw_bytes = (max_tokens / 0.125) * 0.75

    策略:
    1. 若当前已满足 → 直通
    2. 按缩放系数 [1.0, 0.75, 0.5, 0.25] 渐进缩放 + 格式优化
    3. 兜底:400x400 + JPEG q20
    """
    # token 预估 ≈ base64 长度 * 0.125
    estimated_tokens = int(len(img.base64_data) * TOKEN_PER_BASE64_CHAR)
    if estimated_tokens <= max_tokens:
        return img

    target_bytes = max(int(max_tokens / TOKEN_PER_BASE64_CHAR * 0.75), 1024)

    try:
        pil_img = Image.open(io.BytesIO(img.data))
        pil_img.load()
    except Exception as e:  # noqa: BLE001
        # 阶段 1 输出阶段 2 无法再开,属于管线一致性异常
        logger.error("无法重新打开阶段1输出的图片, 走兜底: %s: %s", type(e).__name__, e)
        return _ultra_compressed_fallback(img)

    original_w, original_h = pil_img.size

    for factor in (1.0, 0.75, 0.5, 0.25):
        new_w = max(int(original_w * factor), 1)
        new_h = max(int(original_h * factor), 1)
        resized = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 先试原格式优化
        if img.media_type == "image/png":
            try:
                png_data = _encode_image(resized, "PNG", optimize=True)
                if len(png_data) <= target_bytes:
                    return _build_compressed(
                        png_data,
                        "image/png",
                        new_w,
                        new_h,
                        img.original_size,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[compress_image_with_token_budget] PNG 缩放编码失败,降级到 JPEG: %s: %s",
                    type(e).__name__,
                    e,
                )

        # 再试 JPEG 降级
        jpeg_data = _try_jpeg_qualities(resized, target_bytes, qualities=(80, 50, 20))
        if jpeg_data is not None:
            return _build_compressed(
                jpeg_data, "image/jpeg", new_w, new_h, img.original_size
            )

    return _ultra_compressed_fallback(img, pil_img)


def _ultra_compressed_fallback(
    original: CompressedImage,
    pil_img: Image.Image | None = None,
) -> CompressedImage:
    """最终兜底:400x400 + JPEG q20。"""
    try:
        if pil_img is None:
            pil_img = Image.open(io.BytesIO(original.data))
            pil_img.load()
        pil_img.thumbnail((400, 400), Image.Resampling.LANCZOS)
        ultra_data = _encode_image(pil_img, "JPEG", quality=20)
        return _build_compressed(
            ultra_data,
            "image/jpeg",
            pil_img.width,
            pil_img.height,
            original.original_size,
        )
    except Exception as e:  # noqa: BLE001
        # 兜底失败时返回 stage1 原图(已过 API 5MB 校验),仅 token 预算可能超
        logger.error("兜底压缩失败: %s: %s", type(e).__name__, e, exc_info=True)
        return original


# ─────────────────────────────────────────────────────────────────────────
# 图片入口 (对外)
# ─────────────────────────────────────────────────────────────────────────


def compress_image_bytes(
    raw: bytes,
    max_tokens: int = DEFAULT_IMAGE_MAX_TOKENS,
) -> CompressedImage:
    """原始图片字节 → 两阶段压缩（阶段1 满足 API 硬约束 + 阶段2 满足 token 预算）。

    同步 CPU 密集，调用方应在 asyncio.to_thread 内跑。格式由 magic bytes 内部嗅探。
    """
    stage1 = maybe_resize_and_downsample_image(raw)
    return compress_image_with_token_budget(stage1, max_tokens)


async def read_image_with_token_budget(
    path: Path,
    max_tokens: int = DEFAULT_IMAGE_MAX_TOKENS,
) -> CompressedImage:
    """读取图片文件并压缩到 token 预算内。

    文件 IO 用 asyncio.to_thread 包装,避免阻塞事件循环。
    一次性读入内存后串联两阶段压缩管线,与 Claude Code 的"单次读取"策略一致。
    """

    def _read_bytes() -> bytes:
        return path.read_bytes()

    try:
        raw = await asyncio.to_thread(_read_bytes)
    except OSError as e:
        raise MediaReadError(
            f"无法读取图片文件 {path.name}: {e}",
            hint="请检查文件路径与权限",
        ) from e

    return await asyncio.to_thread(compress_image_bytes, raw, max_tokens)


# ─────────────────────────────────────────────────────────────────────────
# PDF: 共用校验 + 参数解析
# ─────────────────────────────────────────────────────────────────────────


def validate_pdf_bytes(path: Path, max_size: int) -> bytes:
    """读取 PDF 字节并做三层校验:大小、非空、%PDF- magic bytes。

    magic bytes 校验是 *session 保护防线*:若一个伪装成 .pdf 的非 PDF
    文件进入对话上下文,后续每轮 API 调用都会 400,整个 session 无法恢复。
    """
    if not path.exists():
        raise MediaReadError(
            f"PDF 文件不存在: {path}",
            hint="请检查文件路径",
        )

    size = path.stat().st_size
    if size == 0:
        raise MediaReadError(
            f"PDF 文件为空: {path.name}",
            hint="请检查文件是否完整",
        )

    if size > max_size:
        raise MediaReadError(
            f"PDF 文件 {size // 1024 // 1024}MB 超过上限 {max_size // 1024 // 1024}MB",
            hint="请使用 pages 参数分页读取",
        )

    raw = path.read_bytes()
    if not raw.startswith(PDF_MAGIC):
        raise MediaReadError(
            f"文件 {path.name} 不是有效的 PDF (缺少 %PDF- 头)",
            hint="该文件可能是伪装成 .pdf 的其他格式,请用对应工具打开",
        )
    return raw


def parse_pages_param(pages: str, total: int) -> list[int]:
    """解析 pages 参数为 0-indexed 的页码列表。

    支持格式:
        "5"           -> [4]
        "1-5"         -> [0, 1, 2, 3, 4]
        "1,3,5"       -> [0, 2, 4]
        "1-3,7,9-10"  -> [0, 1, 2, 6, 8, 9]

    校验:
    - 所有页码必须是正整数且 <= total
    - 去重后的总页数必须 <= PDF_MAX_PAGES_PER_READ
    - 格式错误抛 MediaReadError
    """
    if not pages or not pages.strip():
        raise MediaReadError(
            "pages 参数为空",
            hint="请使用类似 '1-5' 或 '1,3,5' 的格式",
        )

    result: list[int] = []
    seen: set[int] = set()

    segments = [s.strip() for s in pages.split(",") if s.strip()]
    if not segments:
        raise MediaReadError(
            f"pages 参数格式错误: '{pages}'",
            hint="请使用类似 '1-5' 或 '1,3,5' 的格式",
        )

    for seg in segments:
        if "-" in seg:
            parts = seg.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise MediaReadError(
                    f"pages 范围格式错误: '{seg}'",
                    hint="范围格式应为 'first-last',如 '1-5'",
                )
            try:
                first = int(parts[0])
                last = int(parts[1])
            except ValueError as e:
                raise MediaReadError(
                    f"pages 范围必须为数字: '{seg}'",
                    hint="例如 '1-5'",
                ) from e
            if first < 1 or last < 1 or first > last:
                raise MediaReadError(
                    f"pages 范围非法: '{seg}' (页码从 1 开始,first <= last)",
                    hint="例如 '1-5' 或 '3-3'",
                )
            if last > total:
                raise MediaReadError(
                    f"pages 范围 '{seg}' 超出 PDF 总页数 {total}",
                    hint=f"请使用 1 到 {total} 之间的页码",
                )
            for p in range(first, last + 1):
                if p not in seen:
                    seen.add(p)
                    result.append(p - 1)
        else:
            try:
                p = int(seg)
            except ValueError as e:
                raise MediaReadError(
                    f"pages 项必须为数字: '{seg}'",
                    hint="例如 '5' 或 '1-5'",
                ) from e
            if p < 1:
                raise MediaReadError(
                    f"pages 页码必须 >= 1: '{seg}'",
                    hint="页码从 1 开始计数",
                )
            if p > total:
                raise MediaReadError(
                    f"pages 页码 {p} 超出 PDF 总页数 {total}",
                    hint=f"请使用 1 到 {total} 之间的页码",
                )
            if p not in seen:
                seen.add(p)
                result.append(p - 1)

    if len(result) > PDF_MAX_PAGES_PER_READ:
        raise MediaReadError(
            f"pages 总页数 {len(result)} 超过单次上限 {PDF_MAX_PAGES_PER_READ}",
            hint=f"请分多次调用,每次最多 {PDF_MAX_PAGES_PER_READ} 页",
        )

    return result


# ─────────────────────────────────────────────────────────────────────────
# PDF 渲染路径 (按页渲染为图片)
# ─────────────────────────────────────────────────────────────────────────


async def extract_pdf_pages(
    path: Path,
    page_indices: list[int],
    dpi: int = PDF_RENDER_DPI,
    max_tokens: int = DEFAULT_IMAGE_MAX_TOKENS,
    raw: bytes | None = None,
) -> list[CompressedImage]:
    """把指定页渲染为图片,每页走图片压缩管线。raw 传入可复用已校验字节。"""
    if not page_indices:
        raise MediaReadError(
            "page_indices 为空",
            hint="请传入至少一页",
        )

    per_page_budget = max(
        max_tokens // len(page_indices),
        2000,
    )

    def _work() -> list[CompressedImage]:
        actual_raw = (
            raw if raw is not None else validate_pdf_bytes(path, PDF_MAX_EXTRACT_SIZE)
        )
        results: list[CompressedImage] = []
        with fitz.open(stream=actual_raw, filetype="pdf") as doc:
            total = doc.page_count
            for idx in page_indices:
                if idx < 0 or idx >= total:
                    raise MediaReadError(
                        f"页码索引越界: {idx + 1} (PDF 共 {total} 页)",
                        hint=f"请使用 1 到 {total} 之间的页码",
                    )
                page = doc[idx]
                # 用 fitz 的 get_pixmap 渲染,tobytes("png") 拿到 PNG 字节
                pix = page.get_pixmap(dpi=dpi)
                png_bytes = pix.tobytes("png")

                stage1 = maybe_resize_and_downsample_image(png_bytes, "image/png")
                stage2 = compress_image_with_token_budget(
                    stage1, max_tokens=per_page_budget
                )
                results.append(stage2)
        return results

    return await asyncio.to_thread(_work)

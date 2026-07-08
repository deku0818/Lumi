"""上传图片持久化：把消息里的内联 image block 存盘、路径交还调用方。

所有 channel（desktop / 飞书 / 未来 TUI）上传的图片以 base64 image block 到达 bridge。
本模块在 ``stream_response`` 入口把它们存到 ``~/.lumi/uploads/`` 下并返回路径列表，
由 bridge 与普通文件附件统一拼 ``<attached-file>`` 标签块（模型侧）+ 写进
``lumi.items`` 的 files（显示侧），让 read / vision 工具按路径消费。

为何在后端存盘（而非前端发路径）：图片走 base64 传输，落盘到「后端本机」，本地与远程
后端都能读到（前端本地路径在远程后端上不存在）。集中存 ~/.lumi/uploads 不污染用户项目；
read / vision 为只读工具、不受工作区边界限制（见 permissions.routing），故能直接读取。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import uuid

from lumi.utils.config.global_manager import uploads_dir
from lumi.utils.logger import logger

_MEDIA_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# 单张内联图片解码后大小上限（仅存盘用，read/vision 端会再按 token 预算压缩）
_MAX_IMAGE_BYTES = 50 * 1024 * 1024


def _save_base64_image(media_type: str, data: str) -> str | None:
    """把 base64 图片存到 ``~/.lumi/uploads/`` 下，返回绝对路径；超限/失败返回 None。

    同步 IO + CPU（decode/write），由 persist_image_blocks 经 to_thread 调用，勿在事件循环直调。
    """
    # 按 base64 长度粗估解码大小（≈ len*3/4），超限即拒，不解码大 blob
    if len(data) * 3 // 4 > _MAX_IMAGE_BYTES:
        logger.warning(
            "[uploads] 内联图片超过 %dMB 上限，已跳过", _MAX_IMAGE_BYTES // 1024 // 1024
        )
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("[uploads] base64 解码失败，跳过一张内联图片")
        return None
    if not raw:
        return None
    ext = _MEDIA_EXT.get(media_type, ".png")
    dest_dir = uploads_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex[:12]}{ext}"
    dest.write_bytes(raw)
    return str(dest)


async def persist_image_blocks(content: str | list) -> tuple[str | list, list[str]]:
    """把 content 里的内联 image block 存盘，返回 ``(去图后的 content, 路径列表)``。

    - base64 图片 → 存到 ``~/.lumi/uploads/``，返回其后端本机路径
    - url 图片 → 直接返回 url（vision 工具支持 http(s)）
    - 非 list / 无图片 → content 原样返回、路径为空（不建目录、零副作用）

    decode/写盘经 ``asyncio.to_thread`` 卸载，避免阻塞事件循环（消息热路径）。
    存盘失败（超 50MB / 解码失败）：丢弃原始块并留文本占位块——模型需要知道
    有图被跳过，且绝不把未压缩的 raw base64 内联转发给模型（会超上游图片
    大小上限触发 API 400）。
    """
    if not isinstance(content, list):
        return content, []

    kept: list = []
    paths: list[str] = []
    for block in content:
        src = (
            block.get("source")
            if isinstance(block, dict) and block.get("type") == "image"
            else None
        )
        if isinstance(src, dict):
            if src.get("type") == "base64":
                path = await asyncio.to_thread(
                    _save_base64_image,
                    src.get("media_type", "image/png"),
                    src.get("data", ""),
                )
                if path:
                    paths.append(path)
                else:
                    kept.append(
                        {"type": "text", "text": "[图片过大或无法解析，已跳过]"}
                    )
                continue
            elif src.get("type") == "url" and src.get("url"):
                paths.append(src["url"])
                continue
        kept.append(block)

    return kept, paths

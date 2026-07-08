"""上传图片持久化：内联 image block 存盘（~/.lumi/uploads），路径交还调用方。"""

from __future__ import annotations

import base64

import pytest

from lumi.gateway import uploads as umod

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-bytes").decode("ascii")


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    """把 uploads_dir 重定向到临时目录，避免污染真实 ~/.lumi/uploads。"""
    d = tmp_path / "uploads"
    monkeypatch.setattr(umod, "uploads_dir", lambda: d)
    return d


def _img_block(data: str = _PNG_B64, media_type: str = "image/png") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


async def test_str_content_passthrough():
    assert await umod.persist_image_blocks("你好") == ("你好", [])


async def test_no_image_passthrough(_tmp_uploads):
    content = [{"type": "text", "text": "只有文字"}]
    out, paths = await umod.persist_image_blocks(content)
    assert out == content
    assert paths == []
    assert not _tmp_uploads.exists()  # 无图片不建目录


async def test_base64_image_saved_and_path_returned(_tmp_uploads):
    content = [{"type": "text", "text": "看这张图"}, _img_block()]
    out, paths = await umod.persist_image_blocks(content)
    assert all(b.get("type") != "image" for b in out)
    assert out == [{"type": "text", "text": "看这张图"}]
    saved = list(_tmp_uploads.glob("*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == base64.b64decode(_PNG_B64)
    assert paths == [str(saved[0])]


async def test_multiple_images_all_returned(_tmp_uploads):
    content = [_img_block(), _img_block(), _img_block(media_type="image/jpeg")]
    out, paths = await umod.persist_image_blocks(content)
    assert out == []
    assert len(paths) == 3
    assert len(list(_tmp_uploads.glob("*.png"))) == 2
    assert len(list(_tmp_uploads.glob("*.jpg"))) == 1


async def test_url_image_returns_url_without_saving(_tmp_uploads):
    url = "https://example.com/pic.png"
    content = [{"type": "image", "source": {"type": "url", "url": url}}]
    out, paths = await umod.persist_image_blocks(content)
    assert out == []
    assert paths == [url]
    assert not _tmp_uploads.exists()  # url 不落盘


def _has_raw_image(out):
    return any(isinstance(b, dict) and b.get("type") == "image" for b in out)


def _first_text(out):
    return next(
        b["text"] for b in out if isinstance(b, dict) and b.get("type") == "text"
    )


async def test_invalid_base64_dropped_with_placeholder():
    # base64 解码失败：丢弃原始块、留文本占位块，绝不把 raw base64 内联转发给模型
    bad = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "!!!not-base64!!!",
            },
        }
    ]
    out, paths = await umod.persist_image_blocks(bad)
    assert not _has_raw_image(out)
    assert "已跳过" in _first_text(out)
    assert paths == []


async def test_oversized_image_dropped_with_placeholder(_tmp_uploads):
    # 超过 _MAX_IMAGE_BYTES 上限：不落盘、丢弃原始块、留文本占位（不 raw 转发触发 API 400）
    huge = "A" * (
        (umod._MAX_IMAGE_BYTES + 1) * 4 // 3 + 8
    )  # base64 长度 → 解码后 > 上限
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": huge},
        }
    ]
    out, paths = await umod.persist_image_blocks(content)
    assert not _has_raw_image(out)  # 原始 base64 块被丢弃，不泄漏给模型
    assert "已跳过" in _first_text(out)
    assert paths == []
    assert not _tmp_uploads.exists()

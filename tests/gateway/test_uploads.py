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


async def _chunks(*parts: bytes, fail: BaseException | None = None):
    for p in parts:
        yield p
    if fail is not None:
        raise fail


async def test_save_upload_limit_enforced_while_streaming(_tmp_uploads, monkeypatch):
    # 分块传输不带 Content-Length：上限只能边收边数，超限即中止且不留半截文件
    monkeypatch.setattr(umod, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(umod.UploadTooLarge):
        await umod.save_upload("big.bin", _chunks(b"12345678", b"12345678"))
    assert list(_tmp_uploads.iterdir()) == []


async def test_save_upload_disconnect_removes_partial(_tmp_uploads):
    from starlette.requests import ClientDisconnect

    with pytest.raises(ClientDisconnect):
        await umod.save_upload("a.txt", _chunks(b"half", fail=ClientDisconnect()))
    assert list(_tmp_uploads.iterdir()) == []


async def test_remove_uploads_only_touches_uploads_dir(_tmp_uploads, tmp_path):
    kept = tmp_path / "用户自己的文件.txt"  # 本地后端附件是用户原文件，绝不能删
    kept.write_text("x")
    path = await umod.save_upload("远程.txt", _chunks(b"data"))
    _, (image,) = await umod.persist_image_blocks([_img_block()])
    umod.remove_uploads([path, image, str(kept), "https://example.com/pic.png"])
    assert list(_tmp_uploads.iterdir()) == []  # 独占 uuid 子目录随之删掉
    assert kept.exists()


async def test_delete_thread_removes_declared_uploads(_tmp_uploads, tmp_path):
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import START, MessagesState, StateGraph
    from toy_graph import bridge_with

    kept = tmp_path / "local.txt"
    kept.write_text("x")
    path = await umod.save_upload("远程.txt", _chunks(b"data"))
    files = [{"path": path}, {"path": str(kept)}]
    msg = HumanMessage(
        content="看附件", additional_kwargs={"lumi": {"items": [{"files": files}]}}
    )
    builder = StateGraph(MessagesState)
    builder.add_node("N", lambda _s: {})
    builder.add_edge(START, "N")
    graph = builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-up"}}
    await graph.ainvoke({"messages": [msg]}, config)
    bridge = bridge_with(config, graph)
    bridge._agent.adelete_thread = graph.checkpointer.adelete_thread

    await bridge.delete_thread("t-up")

    assert (await graph.aget_state(config)).values == {}
    assert list(_tmp_uploads.iterdir()) == []
    assert kept.exists()

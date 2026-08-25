"""/upload 附件上行端点：鉴权 / 落盘保原名 / 文件名净化 / 大小闸门 / 预检。"""

from pathlib import Path

import pytest

from lumi.gateway import uploads
from lumi.gateway.channels import ws


@pytest.fixture
def uploads_root(tmp_path, monkeypatch):
    """把落盘目录挪到 tmp（端点经 uploads.save_upload 落盘，故打在 uploads 模块上）。"""
    root = tmp_path / "uploads"
    monkeypatch.setattr(uploads, "uploads_dir", lambda: root)
    return root


async def test_wrong_token_rejected(http_client):
    r = await http_client.post(
        "/upload", params={"name": "a.txt", "token": "wrong"}, content=b"hi"
    )
    assert r.status_code == 401


async def test_saves_content_and_keeps_name(http_client, uploads_root):
    r = await http_client.post(
        "/upload",
        params={"name": "季度报告.pdf", "token": "secret"},
        content=b"%PDF-1.4",
    )
    assert r.status_code == 200
    dest = Path(r.json()["path"])
    assert dest.read_bytes() == b"%PDF-1.4"
    # 显示侧的附件名取 Path(p).name，落盘必须保留原名（不加 uuid 前缀）
    assert dest.name == "季度报告.pdf"
    assert r.headers["access-control-allow-origin"] == "*"


async def test_multi_chunk_body_written_whole(http_client, uploads_root, monkeypatch):
    """跨批次刷盘不丢字节：把攒批阈值压到 8B，强制走多次 write。"""
    monkeypatch.setattr(uploads, "_FLUSH_BYTES", 8)
    body = bytes(range(256)) * 4
    r = await http_client.post(
        "/upload", params={"name": "blob.bin", "token": "secret"}, content=body
    )
    assert Path(r.json()["path"]).read_bytes() == body


async def test_path_traversal_name_stripped(http_client, uploads_root):
    r = await http_client.post(
        "/upload", params={"name": "../../evil.sh", "token": "secret"}, content=b"x"
    )
    dest = Path(r.json()["path"])
    assert dest.name == "evil.sh"
    assert dest.parent.parent == uploads_root


async def test_null_byte_name_400(http_client, uploads_root):
    """NUL 过得了「非空且非 ..」那关，却让 open() 抛 ValueError——须在收流前挡掉。"""
    r = await http_client.post(
        "/upload", params={"name": "a\x00b.txt", "token": "secret"}, content=b"x"
    )
    assert r.status_code == 400


async def test_oversize_413_writes_nothing(http_client, uploads_root, monkeypatch):
    """超限按 Content-Length 在收流前拒：一个字节都不该落盘（无半截文件可回滚）。"""
    monkeypatch.setattr(ws, "_MAX_FILE_BYTES", 4)
    r = await http_client.post(
        "/upload", params={"name": "big.bin", "token": "secret"}, content=b"xxxxxx"
    )
    assert r.status_code == 413
    assert not uploads_root.exists()


async def test_preflight_allows_post(http_client):
    r = await http_client.options("/upload")
    assert r.status_code == 204
    assert r.headers["access-control-allow-methods"] == "POST"

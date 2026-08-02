"""/file 文件通道端点：鉴权 / 404 / 大小上限 / HEAD / CORS（ASGI 直调，不跑 lifespan）。"""

import httpx
import pytest

from lumi.gateway.channels import ws


@pytest.fixture
def client():
    ws.app.state.token = "secret"
    transport = httpx.ASGITransport(app=ws.app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")
    ws.app.state.token = ""


async def test_wrong_token_rejected(client, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    r = await client.get("/file", params={"path": str(f), "token": "wrong"})
    assert r.status_code == 401


async def test_serves_file_with_mime_and_cors(client, tmp_path):
    f = tmp_path / "报告.html"
    f.write_text("<html>ok</html>", encoding="utf-8")
    r = await client.get("/file", params={"path": str(f), "token": "secret"})
    assert r.status_code == 200
    assert r.text == "<html>ok</html>"
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["access-control-allow-origin"] == "*"


async def test_missing_file_404_with_cors(client, tmp_path):
    r = await client.get(
        "/file", params={"path": str(tmp_path / "无.txt"), "token": "secret"}
    )
    assert r.status_code == 404
    assert r.headers["access-control-allow-origin"] == "*"


async def test_directory_404(client, tmp_path):
    r = await client.get("/file", params={"path": str(tmp_path), "token": "secret"})
    assert r.status_code == 404


async def test_head_probe(client, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    r = await client.head("/file", params={"path": str(f), "token": "secret"})
    assert r.status_code == 200
    assert r.content == b""


async def test_oversize_413(client, tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "_MAX_FILE_BYTES", 1)
    f = tmp_path / "big.bin"
    f.write_bytes(b"xx")
    r = await client.get("/file", params={"path": str(f), "token": "secret"})
    assert r.status_code == 413

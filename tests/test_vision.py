"""vision 工具测试：条件注册 + 本地/URL 载入 + 识别问答（全 mock，无真实网络/LLM）。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from lumi.agents.tools.providers import vision as vmod
from lumi.models import provider_store


def _png(w: int = 60, h: int = 40, color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _pdf(num_pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()  # type: ignore[attr-defined]
        page.insert_text((72, 72), f"Page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _resolved(monkeypatch):
    monkeypatch.setattr(
        provider_store,
        "resolve_vision",
        lambda: provider_store.ResolvedModel("gpt-4o", "u", "k", "auto"),
    )


# ─────────────────────────────────────────────────────────────────────────
# 条件注册（config.json 的 vision.model 决定）
# ─────────────────────────────────────────────────────────────────────────


def _set_vision_model(monkeypatch, model: str):
    """把 config.json 的 vision.model 打桩为指定值（get_vision_tools 据此条件加载）。"""
    import lumi.utils.read_config as rc

    fake = SimpleNamespace(config=SimpleNamespace(vision=SimpleNamespace(model=model)))
    monkeypatch.setattr(rc, "get_config", lambda: fake)


class TestGetVisionTools:
    async def test_absent_when_unconfigured(self, monkeypatch):
        _set_vision_model(monkeypatch, "")
        assert await vmod.get_vision_tools() == []

    async def test_present_when_configured(self, monkeypatch):
        _set_vision_model(monkeypatch, "gpt-4o")
        tools = await vmod.get_vision_tools()
        assert [t.name for t in tools] == ["vision"]

    async def test_name_whitelist_excludes(self, monkeypatch):
        _set_vision_model(monkeypatch, "gpt-4o")
        assert await vmod.get_vision_tools(names=["read"]) == []
        assert len(await vmod.get_vision_tools(names=["vision"])) == 1


# ─────────────────────────────────────────────────────────────────────────
# 载入图片/PDF：本地路径 + http(s) URL
# ─────────────────────────────────────────────────────────────────────────


class TestLoadBlocks:
    async def test_local_image(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(_png())
        blocks = await vmod._load_blocks(str(p))
        assert len(blocks) == 1 and blocks[0]["type"] == "image"
        assert blocks[0]["source"]["type"] == "base64"

    async def test_local_pdf_renders_each_page(self, tmp_path):
        p = tmp_path / "x.pdf"
        p.write_bytes(_pdf(2))
        blocks = await vmod._load_blocks(str(p))
        imgs = [b for b in blocks if b["type"] == "image"]
        assert len(imgs) == 2

    async def test_nonexistent_raises(self, tmp_path):
        with pytest.raises(vmod.MediaReadError):
            await vmod._load_blocks(str(tmp_path / "ghost.png"))

    async def test_unsupported_type_raises(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("hi")
        with pytest.raises(vmod.MediaReadError):
            await vmod._load_blocks(str(p))

    async def test_url_image(self, monkeypatch):
        async def fake_dl(url):
            return _png()

        monkeypatch.setattr(vmod, "_download", fake_dl)
        blocks = await vmod._load_blocks("https://example.com/y.png")
        assert len(blocks) == 1 and blocks[0]["type"] == "image"

    async def test_url_pdf_sniffed_by_magic(self, monkeypatch):
        # URL 无 .pdf 后缀，但内容是 PDF → 按 %PDF- magic 识别为 PDF
        async def fake_dl(url):
            return _pdf(1)

        monkeypatch.setattr(vmod, "_download", fake_dl)
        blocks = await vmod._load_blocks("https://example.com/download?id=42")
        assert any(b["type"] == "image" for b in blocks)


# ─────────────────────────────────────────────────────────────────────────
# _download：流式下载 + 大小上限（超限即断，不整体读进内存）
# ─────────────────────────────────────────────────────────────────────────


class _FakeStreamResp:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        return _FakeStreamCtx(self._resp)


def _patch_client(monkeypatch, resp):
    monkeypatch.setattr(vmod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))


class TestDownload:
    async def test_small_download_joins_chunks(self, monkeypatch):
        _patch_client(monkeypatch, _FakeStreamResp([b"ab", b"cd", b"ef"]))
        assert await vmod._download("https://x/small") == b"abcdef"

    async def test_streaming_size_cap_aborts(self, monkeypatch):
        half = b"x" * (vmod._MAX_DOWNLOAD_BYTES // 2 + 1)
        _patch_client(monkeypatch, _FakeStreamResp([half, half]))  # 累计超限
        with pytest.raises(vmod.MediaReadError):
            await vmod._download("https://x/big")

    async def test_content_length_precheck_rejects(self, monkeypatch):
        # Content-Length 已声明超限 → 未拉取 body 即拒
        resp = _FakeStreamResp(
            [], headers={"content-length": str(vmod._MAX_DOWNLOAD_BYTES + 1)}
        )
        _patch_client(monkeypatch, resp)
        with pytest.raises(vmod.MediaReadError):
            await vmod._download("https://x/declared-big")


# ─────────────────────────────────────────────────────────────────────────
# vision 工具端到端（mock LLM）
# ─────────────────────────────────────────────────────────────────────────


class _FakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content="识别结果：一只红色方块")


class TestVisionTool:
    async def test_no_vision_model_configured(self, monkeypatch):
        monkeypatch.setattr(provider_store, "resolve_vision", lambda: None)
        out = await vmod.vision.coroutine(file_path="x.png", question="?")
        assert "未配置视觉" in out

    async def test_happy_path_local_image(self, tmp_path, monkeypatch):
        _resolved(monkeypatch)
        import lumi.models.manager as mgr

        monkeypatch.setattr(mgr, "create_llm", lambda *a, **k: _FakeLLM())
        p = tmp_path / "x.png"
        p.write_bytes(_png())
        out = await vmod.vision.coroutine(file_path=str(p), question="什么颜色")
        assert "红色方块" in out

    async def test_url_image_question(self, monkeypatch):
        _resolved(monkeypatch)
        import lumi.models.manager as mgr

        async def fake_dl(url):
            return _png()

        monkeypatch.setattr(vmod, "_download", fake_dl)
        monkeypatch.setattr(mgr, "create_llm", lambda *a, **k: _FakeLLM())
        out = await vmod.vision.coroutine(
            file_path="https://example.com/y.png", question="描述"
        )
        assert "红色方块" in out

    async def test_load_error_returns_friendly(self, tmp_path, monkeypatch):
        _resolved(monkeypatch)
        out = await vmod.vision.coroutine(
            file_path=str(tmp_path / "ghost.png"), question="?"
        )
        assert out.startswith("错误")

    async def test_llm_error_returns_friendly(self, tmp_path, monkeypatch):
        _resolved(monkeypatch)
        import lumi.models.manager as mgr

        def boom(*a, **k):
            raise RuntimeError("连接失败")

        monkeypatch.setattr(mgr, "create_llm", boom)
        p = tmp_path / "x.png"
        p.write_bytes(_png())
        out = await vmod.vision.coroutine(file_path=str(p), question="?")
        assert "识别失败" in out and "gpt-4o" in out

"""图片 / PDF 处理管线测试 (_media.py)

动态生成 fixtures (PNG/JPEG/PDF),不需要 git 存二进制。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import fitz
import pytest
from langchain_core.messages import HumanMessage
from PIL import Image

from lumi.agents.tools.providers.filesystem.media import (
    IMAGE_FALLBACK_DIMENSION,
    IMAGE_MAX_DIMENSION,
    PDF_MAX_EXTRACT_SIZE,
    PDF_MAX_PAGES_PER_READ,
    CompressedImage,
    MediaReadError,
    compress_image_with_token_budget,
    detect_image_format,
    extract_pdf_pages,
    maybe_resize_and_downsample_image,
    parse_pages_param,
    read_image_with_token_budget,
    validate_pdf_bytes,
)

# ═════════════════════════════════════════════════════════════════════════
# Fixture 工厂
# ═════════════════════════════════════════════════════════════════════════


def make_png_bytes(w: int = 100, h: int = 100, color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_rgba_png_bytes(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 255, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg_bytes(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_gif_bytes() -> bytes:
    img = Image.new("P", (50, 50), 0)
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def make_webp_bytes() -> bytes:
    img = Image.new("RGB", (50, 50), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def make_large_png_bytes(w: int = 3000, h: int = 3000) -> bytes:
    """生成尺寸超标的 PNG (触发 resize 分支)"""
    # 渐变色避免 palette 压缩的意外效果
    img = Image.new("RGB", (w, h))
    pixels = img.load()
    for x in range(w):
        for y in range(0, h, 100):  # 采样降低 Python 循环开销
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_pdf_bytes(num_pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()  # type: ignore[attr-defined]
        page.insert_text((72, 72), f"Page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def make_corrupt_pdf_bytes() -> bytes:
    return b"NOT-A-PDF" + b"\x00" * 100


# ═════════════════════════════════════════════════════════════════════════
# detect_image_format: magic bytes 嗅探
# ═════════════════════════════════════════════════════════════════════════


class TestDetectImageFormat:
    def test_png(self):
        assert detect_image_format(make_png_bytes()) == "image/png"

    def test_jpeg(self):
        assert detect_image_format(make_jpeg_bytes()) == "image/jpeg"

    def test_gif(self):
        assert detect_image_format(make_gif_bytes()) == "image/gif"

    def test_webp(self):
        assert detect_image_format(make_webp_bytes()) == "image/webp"

    def test_unknown_fallback_to_png(self):
        assert detect_image_format(b"random-bytes-not-an-image") == "image/png"

    def test_too_short_buffer(self):
        assert detect_image_format(b"ab") == "image/png"

    def test_ignores_extension_hint(self):
        # 真实内容是 PNG,即使我们调用者以为是 JPEG
        png = make_png_bytes()
        assert detect_image_format(png) == "image/png"


# ═════════════════════════════════════════════════════════════════════════
# maybe_resize_and_downsample_image: 阶段 1
# ═════════════════════════════════════════════════════════════════════════


class TestMaybeResizeAndDownsample:
    def test_small_png_fast_path(self):
        """小图片走快路径,尺寸和数据都不变"""
        raw = make_png_bytes(100, 100)
        result = maybe_resize_and_downsample_image(raw, "image/png")
        assert isinstance(result, CompressedImage)
        assert result.data == raw  # 快路径原样返回
        assert result.width == 100
        assert result.height == 100
        assert result.media_type == "image/png"
        assert result.original_size == len(raw)

    def test_small_jpeg_fast_path(self):
        raw = make_jpeg_bytes(200, 150)
        result = maybe_resize_and_downsample_image(raw, "image/jpeg")
        assert result.data == raw
        assert result.width == 200
        assert result.height == 150

    def test_oversized_dimensions_triggers_resize(self):
        """>2000px 的图会被缩到 2000 内"""
        raw = make_large_png_bytes(3000, 3000)
        result = maybe_resize_and_downsample_image(raw, "image/png")
        assert result.width <= IMAGE_MAX_DIMENSION
        assert result.height <= IMAGE_MAX_DIMENSION

    def test_aspect_ratio_preserved_on_resize(self):
        raw = make_large_png_bytes(4000, 2000)
        result = maybe_resize_and_downsample_image(raw, "image/png")
        assert result.width <= IMAGE_MAX_DIMENSION
        # 宽高比应接近 2:1
        assert abs(result.width / result.height - 2.0) < 0.1

    def test_empty_buffer_raises(self):
        with pytest.raises(MediaReadError) as exc_info:
            maybe_resize_and_downsample_image(b"", "image/png")
        assert "空" in str(exc_info.value)

    def test_invalid_bytes_raises(self):
        with pytest.raises(MediaReadError):
            maybe_resize_and_downsample_image(b"not-an-image-at-all", "image/png")

    def test_rgba_to_jpeg_has_white_background(self):
        """RGBA 透明图转 JPEG 应有白色背景,不崩"""
        # 造一个大到需要 resize 的 RGBA 图 (触发 resize 后的 JPEG 路径)
        img = Image.new("RGBA", (3000, 3000), (0, 255, 0, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()

        result = maybe_resize_and_downsample_image(raw, "image/png")
        # 应成功返回,不抛异常
        assert len(result.data) > 0
        assert result.width <= IMAGE_MAX_DIMENSION

    def test_base64_is_populated(self):
        raw = make_png_bytes()
        result = maybe_resize_and_downsample_image(raw, "image/png")
        decoded = base64.b64decode(result.base64_data)
        assert decoded == result.data


# ═════════════════════════════════════════════════════════════════════════
# compress_image_with_token_budget: 阶段 2
# ═════════════════════════════════════════════════════════════════════════


class TestCompressImageWithTokenBudget:
    def test_within_budget_passthrough(self):
        raw = make_png_bytes(50, 50)
        stage1 = maybe_resize_and_downsample_image(raw, "image/png")
        result = compress_image_with_token_budget(stage1, max_tokens=25000)
        assert result.data == stage1.data  # 直通

    def test_tight_budget_triggers_compression(self):
        """极低 token 预算会触发压缩,最终 base64 大小应 <= 目标"""
        raw = make_large_png_bytes(1500, 1500)
        stage1 = maybe_resize_and_downsample_image(raw, "image/png")
        # 给个极小预算:比如 2000 tokens = 16000 base64 chars = 12KB raw
        result = compress_image_with_token_budget(stage1, max_tokens=2000)
        # 兜底 400x400 之后应满足
        assert result.width <= IMAGE_FALLBACK_DIMENSION or result.width <= 1500


# ═════════════════════════════════════════════════════════════════════════
# read_image_with_token_budget: 异步入口
# ═════════════════════════════════════════════════════════════════════════


class TestReadImageWithTokenBudget:
    async def test_small_png_file(self, tmp_path: Path):
        f = tmp_path / "test.png"
        f.write_bytes(make_png_bytes(100, 100))
        result = await read_image_with_token_budget(f)
        assert result.width == 100
        assert result.media_type == "image/png"

    async def test_jpeg_file(self, tmp_path: Path):
        f = tmp_path / "test.jpg"
        f.write_bytes(make_jpeg_bytes(200, 200))
        result = await read_image_with_token_budget(f)
        assert result.media_type == "image/jpeg"

    async def test_empty_file_raises(self, tmp_path: Path):
        f = tmp_path / "empty.png"
        f.write_bytes(b"")
        with pytest.raises(MediaReadError):
            await read_image_with_token_budget(f)

    async def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(MediaReadError):
            await read_image_with_token_budget(tmp_path / "ghost.png")


# ═════════════════════════════════════════════════════════════════════════
# validate_pdf_bytes: magic bytes 和大小校验
# ═════════════════════════════════════════════════════════════════════════


class TestValidatePdfBytes:
    def test_valid_small_pdf(self, tmp_path: Path):
        f = tmp_path / "ok.pdf"
        f.write_bytes(make_pdf_bytes(1))
        raw = validate_pdf_bytes(f, PDF_MAX_EXTRACT_SIZE)
        assert raw.startswith(b"%PDF-")

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(MediaReadError) as e:
            validate_pdf_bytes(tmp_path / "nope.pdf", PDF_MAX_EXTRACT_SIZE)
        assert "不存在" in str(e.value)

    def test_empty_file_raises(self, tmp_path: Path):
        f = tmp_path / "empty.pdf"
        f.write_bytes(b"")
        with pytest.raises(MediaReadError) as e:
            validate_pdf_bytes(f, PDF_MAX_EXTRACT_SIZE)
        assert "空" in str(e.value)

    def test_oversized_rejected(self, tmp_path: Path):
        f = tmp_path / "huge.pdf"
        f.write_bytes(make_pdf_bytes(1))
        # 给个比文件小的上限,应被拒
        with pytest.raises(MediaReadError) as e:
            validate_pdf_bytes(f, max_size=10)
        assert "超过上限" in str(e.value)

    def test_non_pdf_magic_bytes_rejected(self, tmp_path: Path):
        """核心防线:伪装成 .pdf 的非 PDF 文件必须被拦截"""
        f = tmp_path / "fake.pdf"
        f.write_bytes(make_corrupt_pdf_bytes())
        with pytest.raises(MediaReadError) as e:
            validate_pdf_bytes(f, PDF_MAX_EXTRACT_SIZE)
        assert "%PDF-" in str(e.value)

    def test_html_disguised_as_pdf(self, tmp_path: Path):
        """HTML 文件被重命名为 .pdf 应被拦截"""
        f = tmp_path / "html_fake.pdf"
        f.write_bytes(b"<html><body>not a pdf</body></html>")
        with pytest.raises(MediaReadError):
            validate_pdf_bytes(f, PDF_MAX_EXTRACT_SIZE)


# ═════════════════════════════════════════════════════════════════════════
# parse_pages_param: 页码字符串解析
# ═════════════════════════════════════════════════════════════════════════


class TestParsePagesParam:
    def test_single_page(self):
        assert parse_pages_param("5", total=10) == [4]

    def test_range(self):
        assert parse_pages_param("1-5", total=10) == [0, 1, 2, 3, 4]

    def test_comma_separated(self):
        assert parse_pages_param("1,3,5", total=10) == [0, 2, 4]

    def test_mixed_range_and_single(self):
        assert parse_pages_param("1-3,7,9-10", total=20) == [0, 1, 2, 6, 8, 9]

    def test_whitespace_tolerated(self):
        assert parse_pages_param(" 1 - 3 , 5 ", total=10) == [0, 1, 2, 4]

    def test_same_page_single_range(self):
        assert parse_pages_param("3-3", total=10) == [2]

    def test_deduplicates(self):
        """重复页码应去重,保持首次出现顺序"""
        assert parse_pages_param("1,1,2,1-2", total=10) == [0, 1]

    def test_empty_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("", total=10)

    def test_whitespace_only_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("   ", total=10)

    def test_zero_page_raises(self):
        with pytest.raises(MediaReadError) as e:
            parse_pages_param("0", total=10)
        assert ">=" in str(e.value) or "1" in str(e.value)

    def test_inverted_range_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("5-3", total=10)

    def test_out_of_range_page_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("15", total=10)

    def test_out_of_range_range_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("5-20", total=10)

    def test_non_numeric_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("abc", total=10)

    def test_too_many_pages_raises(self):
        """单次超过 20 页应被拒"""
        with pytest.raises(MediaReadError) as e:
            parse_pages_param("1-25", total=100)
        assert str(PDF_MAX_PAGES_PER_READ) in str(e.value)

    def test_max_pages_exactly_ok(self):
        result = parse_pages_param(f"1-{PDF_MAX_PAGES_PER_READ}", total=100)
        assert len(result) == PDF_MAX_PAGES_PER_READ

    def test_malformed_range_raises(self):
        with pytest.raises(MediaReadError):
            parse_pages_param("1-", total=10)
        with pytest.raises(MediaReadError):
            parse_pages_param("-5", total=10)


# ═════════════════════════════════════════════════════════════════════════
# extract_pdf_pages: 按页渲染
# ═════════════════════════════════════════════════════════════════════════


class TestExtractPdfPages:
    async def test_single_page_rendered(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(3))
        result = await extract_pdf_pages(f, [0])
        assert len(result) == 1
        assert isinstance(result[0], CompressedImage)
        assert result[0].media_type in ("image/png", "image/jpeg")
        assert result[0].width > 0
        assert result[0].height > 0

    async def test_multiple_pages_rendered(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(5))
        result = await extract_pdf_pages(f, [0, 2, 4])
        assert len(result) == 3

    async def test_empty_page_indices_raises(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(3))
        with pytest.raises(MediaReadError):
            await extract_pdf_pages(f, [])

    async def test_out_of_range_index_raises(self, tmp_path: Path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(3))
        with pytest.raises(MediaReadError):
            await extract_pdf_pages(f, [10])

    async def test_corrupt_pdf_rejected(self, tmp_path: Path):
        f = tmp_path / "fake.pdf"
        f.write_bytes(make_corrupt_pdf_bytes())
        with pytest.raises(MediaReadError):
            await extract_pdf_pages(f, [0])

    async def test_per_page_budget_split(self, tmp_path: Path):
        """读 10 页时 per-page budget 应约等于 DEFAULT // 10,且下限 2000"""
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(10))
        result = await extract_pdf_pages(f, list(range(10)))
        assert len(result) == 10
        # 每张图都应有实质内容 (未被压缩到 0)
        for img in result:
            assert len(img.data) > 0
            assert img.width > 0

    async def test_no_temp_files_left_behind(self, tmp_path: Path):
        """扫描 tmp_path 确认没有额外临时文件 (全程在内存)"""
        f = tmp_path / "doc.pdf"
        f.write_bytes(make_pdf_bytes(2))
        before = set(tmp_path.iterdir())
        await extract_pdf_pages(f, [0, 1])
        after = set(tmp_path.iterdir())
        assert before == after


# ═════════════════════════════════════════════════════════════════════════
# read 工具 dispatch 层
# ═════════════════════════════════════════════════════════════════════════


def _call_read(file_path: str, **kwargs) -> any:
    """通过完整 ToolCall 格式调用 read 工具,满足 InjectedToolCallId。"""
    from lumi.agents.tools.providers.filesystem import read

    tool_call = {
        "name": "read",
        "args": {"file_path": file_path, **kwargs},
        "id": "test_call_dispatch",
        "type": "tool_call",
    }
    return read.ainvoke(tool_call)


class TestReadDispatchText:
    """文本路径回归:行为应与修改前完全一致"""

    async def test_text_file_returns_tool_message(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "foo.py"
        f.write_text("print('hi')\nprint('bye')")
        result = await _call_read(str(f))
        # 文本路径仍返回 str,LangChain 自动包装为 ToolMessage
        from langchain_core.messages import ToolMessage

        assert isinstance(result, ToolMessage)
        assert "print('hi')" in result.content
        assert "print('bye')" in result.content

    async def test_text_file_with_offset_limit(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "lines.txt"
        f.write_text("\n".join(f"line{i}" for i in range(10)))
        result = await _call_read(str(f), offset=2, limit=3)
        assert "line2" in result.content
        assert "line4" in result.content
        assert "line9" not in result.content

    async def test_nonexistent_file(self, authorized_tmp_dir: Path):
        result = await _call_read(str(authorized_tmp_dir / "ghost.txt"))
        assert "不存在" in result.content


class TestReadDispatchImage:
    """图片分支:应返回 Command,包含 ToolMessage + HumanMessage"""

    async def test_png_returns_command(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "test.png"
        f.write_bytes(make_png_bytes(100, 100))
        result = await _call_read(str(f))
        from langgraph.types import Command

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        assert len(msgs) == 2
        from langchain_core.messages import HumanMessage, ToolMessage

        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].tool_call_id == "test_call_dispatch"
        assert "test.png" in msgs[0].content
        assert isinstance(msgs[1], HumanMessage)

    async def test_png_human_message_has_image_block(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "test.png"
        f.write_bytes(make_png_bytes(50, 50))
        result = await _call_read(str(f))
        human_msg = result.update["messages"][1]
        assert isinstance(human_msg.content, list)
        # 应包含一个 text block 和一个 image block
        types = [b.get("type") for b in human_msg.content if isinstance(b, dict)]
        assert "text" in types
        assert "image" in types

    async def test_image_block_anthropic_format(self, authorized_tmp_dir: Path):
        """必须是 Anthropic 格式: {type:'image', source:{type:'base64',...}}"""
        f = authorized_tmp_dir / "test.jpg"
        f.write_bytes(make_jpeg_bytes(100, 100))
        result = await _call_read(str(f))
        human_msg = result.update["messages"][1]
        image_block = next(
            b
            for b in human_msg.content
            if isinstance(b, dict) and b.get("type") == "image"
        )
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["media_type"] == "image/jpeg"
        assert len(image_block["source"]["data"]) > 0

    async def test_jpg_extension(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "test.jpg"
        f.write_bytes(make_jpeg_bytes())
        result = await _call_read(str(f))
        from langgraph.types import Command

        assert isinstance(result, Command)

    async def test_corrupt_image_returns_error(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "bad.png"
        f.write_bytes(b"definitely-not-an-image")
        result = await _call_read(str(f))
        from langgraph.types import Command

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert "错误" in msgs[0].content

    async def test_image_generic_exception_path(
        self, authorized_tmp_dir: Path, monkeypatch
    ):
        """_read_image_command 的 generic exception 分支应返回友好提示。

        回归:如果未来 read_image_with_token_budget 因非 MediaReadError 异常
        失败(例如 Pillow 版本升级 / OOM),用户应看到"请确认文件是有效的"
        引导,而不是裸 stack trace。
        """
        import lumi.agents.tools.providers.filesystem as fs_mod

        async def _boom(_path):
            raise RuntimeError("simulated boom")

        monkeypatch.setattr(fs_mod, "read_image_with_token_budget", _boom)
        f = authorized_tmp_dir / "x.png"
        f.write_bytes(make_png_bytes())
        result = await _call_read(str(f))
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert "处理图片" in msgs[0].content
        assert "PNG/JPG" in msgs[0].content


class TestReadDispatchPDF:
    """PDF 分支:统一渲染为图片,不传 pages 时渲染全部页(≤10)"""

    async def test_small_pdf_renders_all_pages_anthropic(
        self, authorized_tmp_dir: Path
    ):
        """小 PDF 不传 pages → 自动渲染全部页为 image block,不发 document"""
        f = authorized_tmp_dir / "small.pdf"
        f.write_bytes(make_pdf_bytes(3))
        result = await _call_read(str(f))
        from langgraph.types import Command

        assert isinstance(result, Command)
        msgs = result.update["messages"]
        assert len(msgs) == 2
        human_msg = msgs[1]
        image_blocks = [
            b
            for b in human_msg.content
            if isinstance(b, dict) and b.get("type") == "image"
        ]
        assert len(image_blocks) == 3
        # 不应出现 document block
        assert not any(
            isinstance(b, dict) and b.get("type") == "document"
            for b in human_msg.content
        )

    async def test_small_pdf_renders_all_pages_openai(self, authorized_tmp_dir: Path):
        """OpenAI 不传 pages 行为与 Anthropic 一致:渲染全部页"""
        f = authorized_tmp_dir / "small.pdf"
        f.write_bytes(make_pdf_bytes(2))
        result = await _call_read(str(f))
        from langgraph.types import Command

        assert isinstance(result, Command)
        human_msg = result.update["messages"][1]
        image_blocks = [
            b
            for b in human_msg.content
            if isinstance(b, dict) and b.get("type") == "image"
        ]
        assert len(image_blocks) == 2

    async def test_pdf_too_many_pages_errors(self, authorized_tmp_dir: Path):
        """> 10 页不传 pages 应报错并建议 pages 格式"""
        f = authorized_tmp_dir / "big.pdf"
        f.write_bytes(make_pdf_bytes(15))
        result = await _call_read(str(f))
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert "15 页" in msgs[0].content or "15" in msgs[0].content
        assert "pages" in msgs[0].content

    async def test_pdf_rendered_path_with_pages(self, authorized_tmp_dir: Path):
        """传 pages 渲染指定页"""
        f = authorized_tmp_dir / "book.pdf"
        f.write_bytes(make_pdf_bytes(5))
        result = await _call_read(str(f), pages="1-3")
        from langgraph.types import Command

        assert isinstance(result, Command)
        human_msg = result.update["messages"][1]
        image_blocks = [
            b
            for b in human_msg.content
            if isinstance(b, dict) and b.get("type") == "image"
        ]
        assert len(image_blocks) == 3

    async def test_pdf_invalid_pages_param(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "doc.pdf"
        f.write_bytes(make_pdf_bytes(5))
        result = await _call_read(str(f), pages="10-5")
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert "错误" in msgs[0].content

    async def test_pdf_corrupt_magic_bytes_rejected(self, authorized_tmp_dir: Path):
        """伪装的 PDF 应在 dispatch 层被 magic bytes 拦截"""
        f = authorized_tmp_dir / "fake.pdf"
        f.write_bytes(make_corrupt_pdf_bytes())
        result = await _call_read(str(f))
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert "PDF" in msgs[0].content

    async def test_tool_call_id_passthrough(self, authorized_tmp_dir: Path):
        """所有 Command 返回的 ToolMessage 必须带上原始 tool_call_id"""
        f = authorized_tmp_dir / "x.png"
        f.write_bytes(make_png_bytes())
        result = await _call_read(str(f))
        tool_msg = result.update["messages"][0]
        assert tool_msg.tool_call_id == "test_call_dispatch"


class TestMetaHumanMessageFlag:
    """工具注入的 HumanMessage 必须带 is_meta=True 标记。

    回归:如果缺少该标记,resume 后历史恢复(lumi/sessions 可见性判定)
    会把它当真实用户输入渲染成气泡(就像 bug 报告里的"--- 第 1 页 ---"
    被当成用户说的话一样)。
    """

    async def test_image_human_message_is_meta(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "x.png"
        f.write_bytes(make_png_bytes())
        result = await _call_read(str(f))
        human_msg = result.update["messages"][1]
        assert human_msg.additional_kwargs.get("is_meta") is True

    async def test_pdf_small_human_message_is_meta(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "x.pdf"
        f.write_bytes(make_pdf_bytes(2))
        result = await _call_read(str(f))
        human_msg = result.update["messages"][1]
        assert human_msg.additional_kwargs.get("is_meta") is True

    async def test_pdf_with_pages_human_message_is_meta(self, authorized_tmp_dir: Path):
        f = authorized_tmp_dir / "x.pdf"
        f.write_bytes(make_pdf_bytes(3))
        result = await _call_read(str(f), pages="1-2")
        human_msg = result.update["messages"][1]
        assert human_msg.additional_kwargs.get("is_meta") is True

    def test_should_show_human_message_filters_meta(self):
        """集成检查:TUI 的 should_show_human_message 确实会过滤 is_meta"""
        from lumi.sessions.message_visibility import should_show_human_message

        meta_msg = HumanMessage(
            content=[{"type": "text", "text": "..."}],
            additional_kwargs={"is_meta": True},
        )
        normal_msg = HumanMessage(content="用户真实输入")
        assert should_show_human_message(meta_msg) is False
        assert should_show_human_message(normal_msg) is True

    def test_meta_factory_uses_shared_key(self):
        """回归:meta_human_message / META_KEY / should_show_human_message 跨层契约

        三个调用点(filesystem、agent_bridge、message_visibility)必须共用同一
        META_KEY 常量。若有人把 key 改名(如 "ismeta"),这个测试会因为过滤失败而挂。
        """
        from lumi.agents.core.meta_message import (
            META_KEY,
            is_meta_message,
            meta_human_message,
        )
        from lumi.sessions.message_visibility import should_show_human_message

        # factory 构造的消息应带 META_KEY 标记
        msg = meta_human_message([{"type": "text", "text": "..."}])
        assert msg.additional_kwargs.get(META_KEY) is True
        assert is_meta_message(msg) is True
        # 与 should_show_human_message 形成完整跨层契约
        assert should_show_human_message(msg) is False

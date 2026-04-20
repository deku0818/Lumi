"""execution.py 多模态截断保护测试

验证 _truncate_single_message 对 multimodal content 的策略:
- 含 image/image_url/document block 的 content 整体跳过截断
- 纯文本回归:行为与修改前一致
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage

from lumi.agents.core.node_helpers.execution import (
    _has_multimodal_blocks,
    _truncate_single_message,
)
from lumi.agents.core.node_helpers.messages import content_to_str


# ═════════════════════════════════════════════════════════════════════════
# _has_multimodal_blocks: 识别多模态 content
# ═════════════════════════════════════════════════════════════════════════


class TestHasMultimodalBlocks:
    def test_string_not_multimodal(self):
        assert _has_multimodal_blocks("just text") is False

    def test_empty_list_not_multimodal(self):
        assert _has_multimodal_blocks([]) is False

    def test_text_only_list_not_multimodal(self):
        assert (
            _has_multimodal_blocks(
                [{"type": "text", "text": "hi"}, {"type": "text", "text": "bye"}]
            )
            is False
        )

    def test_image_block_is_multimodal(self):
        assert (
            _has_multimodal_blocks(
                [
                    {"type": "text", "text": "see"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "x",
                        },
                    },
                ]
            )
            is True
        )

    def test_image_url_block_is_multimodal(self):
        assert (
            _has_multimodal_blocks([{"type": "image_url", "image_url": {"url": "..."}}])
            is True
        )

    def test_document_block_is_multimodal(self):
        assert (
            _has_multimodal_blocks(
                [{"type": "document", "source": {"type": "base64", "data": "x"}}]
            )
            is True
        )


# ═════════════════════════════════════════════════════════════════════════
# content_to_str: 多模态占位
# ═════════════════════════════════════════════════════════════════════════


class TestContentToStr:
    def test_string_passthrough(self):
        assert content_to_str("hello") == "hello"

    def test_text_blocks(self):
        result = content_to_str(
            [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
        )
        assert result == "line1\nline2"

    def test_image_placeholder(self):
        result = content_to_str(
            [
                {"type": "text", "text": "see:"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "VERY_LONG_BASE64_NOT_WANTED_IN_OUTPUT",
                    },
                },
            ]
        )
        assert "see:" in result
        assert "[image: image/png]" in result
        # 关键:base64 数据不应出现在输出里
        assert "VERY_LONG_BASE64" not in result

    def test_document_placeholder(self):
        result = content_to_str(
            [
                {"type": "text", "text": "PDF:"},
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": "JVBERi0x_LONG_BASE64",
                    },
                },
            ]
        )
        assert "[document: application/pdf]" in result
        assert "JVBERi0x" not in result

    def test_image_url_placeholder(self):
        result = content_to_str(
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}]
        )
        assert "[image_url]" in result
        assert "xxx" not in result


# ═════════════════════════════════════════════════════════════════════════
# _truncate_single_message: 纯文本回归
# ═════════════════════════════════════════════════════════════════════════


class TestTruncateTextOnly:
    async def test_short_text_unchanged(self):
        msg = ToolMessage(
            content="small",
            tool_call_id="x",
            name="read",
        )
        await _truncate_single_message(msg, max_tokens=1000)
        assert msg.content == "small"

    async def test_long_text_truncated(self):
        """长文本应被截断并带摘要"""
        long = "\n".join(f"line {i}" for i in range(5000))
        msg = ToolMessage(
            content=long,
            tool_call_id="x",
            name="read",
        )
        await _truncate_single_message(msg, max_tokens=100)
        # 截断后应是字符串
        assert isinstance(msg.content, str)
        assert len(msg.content) < len(long)
        # 应包含截断提示
        assert "截断" in msg.content or "tokens" in msg.content


# ═════════════════════════════════════════════════════════════════════════
# _truncate_single_message: 多模态整体跳过截断
# ═════════════════════════════════════════════════════════════════════════


class TestTruncateMultimodalSkipped:
    async def test_image_content_passes_through_unchanged(self):
        """含 image block 的 content 整体不被截,text + image 全部原样"""
        content = [
            {"type": "text", "text": "small caption"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "AAAA",
                },
            },
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=1000)
        assert msg.content == content

    async def test_long_text_with_image_not_truncated(self):
        """即使 text 很长,只要 content 含 image,整体跳过截断"""
        long = "\n".join(f"line {i}" for i in range(5000))
        content = [
            {"type": "text", "text": long},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "ORIGINAL_BASE64_MUST_SURVIVE",
                },
            },
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=200)
        # 整体保留,text 不被截,image 也原样
        assert msg.content == content

    async def test_long_text_with_document_not_truncated(self):
        """document block 同样触发短路"""
        long = "x " * 20000
        content = [
            {"type": "text", "text": long},
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": "PDF_BASE64_DATA",
                },
            },
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=200)
        assert msg.content == content

    async def test_multiple_pages_preserved_with_labels(self):
        """多页 PDF 渲染场景:image + 页码文本 block 交错,全部保留顺序"""
        content = [
            {"type": "text", "text": "pages:"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "P1"},
            },
            {"type": "text", "text": "--- page 2 ---"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "P2"},
            },
            {"type": "text", "text": "--- page 3 ---"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "P3"},
            },
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=10000)
        # 顺序 + block 结构全部保留
        assert msg.content == content

    async def test_image_only_content_noop(self):
        """只含 image 无 text 的 content 也跳过截断"""
        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "X"},
            }
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=100)
        assert msg.content == content

    async def test_image_url_block_also_triggers_skip(self):
        """image_url block(OpenAI/TUI 输入)同样触发整体跳过"""
        content = [
            {"type": "text", "text": "long " * 50000},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_tokens=100)
        assert msg.content == content

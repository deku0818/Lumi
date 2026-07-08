"""execution.py 截断/卸载策略测试

- _truncate_single_message 对 multimodal content 整体跳过截断；纯文本回归
- _try_offload_to_file 替换文本附头部预览（P1b）
- truncate_tool_results 单轮聚合上限（P1a）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import HumanMessage, ToolMessage

import lumi.utils.paths
from lumi.agents.core.node_helpers import execution
from lumi.agents.core.node_helpers.execution import (
    _has_multimodal_blocks,
    _truncate_single_message,
    _try_offload_to_file,
    truncate_tool_results,
)
from lumi.agents.core.node_helpers.messages import content_to_str
from lumi.utils.sizing import content_size

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
        await _truncate_single_message(msg, max_bytes=1000)
        assert msg.content == "small"

    async def test_long_text_truncated(self):
        """长文本应被截断并带摘要"""
        long = "\n".join(f"line {i}" for i in range(5000))
        msg = ToolMessage(
            content=long,
            tool_call_id="x",
            name="read",
        )
        await _truncate_single_message(msg, max_bytes=100)
        # 截断后应是字符串
        assert isinstance(msg.content, str)
        assert len(msg.content) < len(long)
        # 应包含截断提示
        assert "截断" in msg.content or "字节" in msg.content


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
        await _truncate_single_message(msg, max_bytes=1000)
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
        await _truncate_single_message(msg, max_bytes=200)
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
        await _truncate_single_message(msg, max_bytes=200)
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
        await _truncate_single_message(msg, max_bytes=10000)
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
        await _truncate_single_message(msg, max_bytes=100)
        assert msg.content == content

    async def test_image_url_block_also_triggers_skip(self):
        """image_url block(OpenAI/TUI 输入)同样触发整体跳过"""
        content = [
            {"type": "text", "text": "long " * 50000},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        msg = HumanMessage(content=list(content))
        await _truncate_single_message(msg, max_bytes=100)
        assert msg.content == content


# ═════════════════════════════════════════════════════════════════════════
# _try_offload_to_file: 落盘附头部预览（P1b）
# ═════════════════════════════════════════════════════════════════════════


class TestOffloadPreview:
    async def test_replacement_contains_path_and_preview(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lumi.utils.paths, "LUMI_TMP_ROOT", tmp_path / "root")
        content = "\n".join(f"line {i}: some payload text" for i in range(500))

        replacement = await _try_offload_to_file("bash", content, max_bytes=1000)

        assert replacement is not None
        assert "已卸载到文件" in replacement and "预览" in replacement
        # 预览是原文开头且止于换行边界（不留半行）——格式 ---\n{preview}\n---
        preview = replacement.split("---\n")[1].removesuffix("\n")
        assert content.startswith(preview)
        assert content[len(preview)] == "\n"
        # 落盘文件为完整原文
        offload_dir = tmp_path / "root" / "offload"
        files = list(offload_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == content

    async def test_short_content_preview_is_whole(self, tmp_path, monkeypatch):
        """内容短于预览上限时预览即全文，不做换行回退"""
        monkeypatch.setattr(lumi.utils.paths, "LUMI_TMP_ROOT", tmp_path / "root")
        content = "short line 1\nshort line 2"
        replacement = await _try_offload_to_file("bash", content, max_bytes=10)
        assert content in replacement

    async def test_leading_newline_long_line_keeps_preview(self, tmp_path, monkeypatch):
        """唯一换行在开头 + 超长单行：换行回退不得把预览削成空串"""
        monkeypatch.setattr(lumi.utils.paths, "LUMI_TMP_ROOT", tmp_path / "root")
        content = "\n" + "x" * 3000
        replacement = await _try_offload_to_file("bash", content, max_bytes=10)
        preview = replacement.split("---\n")[1].removesuffix("\n")
        assert "xxx" in preview  # 非空，保留了 ~2000 字节的开头


# ═════════════════════════════════════════════════════════════════════════
# truncate_tool_results: 单轮聚合上限（P1a）
# ═════════════════════════════════════════════════════════════════════════


def _fake_config(once_max: int, round_max: int):
    return SimpleNamespace(
        config=SimpleNamespace(
            token=SimpleNamespace(
                once_tool_max_bytes=once_max, round_tool_max_bytes=round_max
            )
        )
    )


def _read_msg(size: int, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content="x" * size, tool_call_id=tool_call_id, name="read")


class TestRoundToolBudget:
    async def test_under_budget_unchanged(self):
        """逐条合规且合计未超预算：内容逐字节不变（回归）"""
        msgs = [_read_msg(500, "a"), _read_msg(500, "b")]
        with patch.object(
            execution, "get_config", return_value=_fake_config(60000, 180000)
        ):
            await truncate_tool_results(msgs)
        assert msgs[0].content == "x" * 500
        assert msgs[1].content == "x" * 500

    async def test_over_budget_tightens_cap_to_fair_share(self):
        """合计超预算：单条上限收紧为公平份额，超份额者被截、小消息不动"""
        msgs = [_read_msg(9000, "a"), _read_msg(500, "b"), _read_msg(500, "c")]
        with patch.object(
            execution, "get_config", return_value=_fake_config(60000, 6000)
        ):
            await truncate_tool_results(msgs)
        # 公平份额 = max(6000//3, 4096) = 4096：大消息被截且带分页提示
        assert content_size(msgs[0].content) < 9000
        assert "offset" in msgs[0].content and "limit" in msgs[0].content
        # 截断元信息描述真实原始输出（单遍处理，不会基于中间产物）
        assert "9000 字节" in msgs[0].content
        assert msgs[1].content == "x" * 500
        assert msgs[2].content == "x" * 500
        assert sum(content_size(m.content) for m in msgs) <= 6000

    async def test_multimodal_not_candidate(self):
        """多模态消息不进聚合候选：内容不变、也不计入总量"""
        image_msg = HumanMessage(
            content=[
                {"type": "text", "text": "y" * 9000},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "Z",
                    },
                },
            ]
        )
        text_msg = _read_msg(500, "a")
        with patch.object(
            execution, "get_config", return_value=_fake_config(60000, 4000)
        ):
            await truncate_tool_results([image_msg, text_msg])
        assert isinstance(image_msg.content, list)  # 未被截断
        assert text_msg.content == "x" * 500  # 文本总量 500 未超预算

    async def test_non_read_offloaded_and_shrinks(self, tmp_path, monkeypatch):
        """非 read 工具超公平份额时走卸载（带预览），且替换后严格变小"""
        monkeypatch.setattr(lumi.utils.paths, "LUMI_TMP_ROOT", tmp_path / "root")
        bash_msg = ToolMessage(
            content="\n".join(f"line {i}: payload" for i in range(600)),  # ~9KB
            tool_call_id="a",
            name="bash",
        )
        original_size = content_size(bash_msg.content)
        with patch.object(
            execution, "get_config", return_value=_fake_config(60000, 6000)
        ):
            await truncate_tool_results([bash_msg])
        assert "已卸载到文件" in bash_msg.content
        assert "预览" in bash_msg.content
        assert content_size(bash_msg.content) < original_size  # 收缩恒真收缩

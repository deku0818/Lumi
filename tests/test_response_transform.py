"""message_transform 跨 provider 转换测试

- image (base64) → image_url (data URL) / image (url) → image_url (raw URL)
- Anthropic / Bedrock 原样透传
"""

from __future__ import annotations


from lumi.agents.core.response import message_transform


# ═════════════════════════════════════════════════════════════════════════
# Anthropic:原样透传
# ═════════════════════════════════════════════════════════════════════════


class TestAnthropicPassthrough:
    async def test_base64_image_passthrough(self):
        content = [
            {"type": "text", "text": "hello"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo",
                },
            },
        ]
        result = await message_transform(content, model_name="claude-sonnet-4-5")
        assert result == content

    async def test_document_block_passthrough(self):
        content = [
            {"type": "text", "text": "see PDF"},
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": "JVBERi0x",
                },
            },
        ]
        result = await message_transform(content, model_name="claude-opus-4")
        assert result == content

    async def test_string_content_returns_unchanged(self):
        result = await message_transform("plain text", model_name="claude-sonnet-4-5")
        assert result == "plain text"


# ═════════════════════════════════════════════════════════════════════════
# OpenAI:base64 → data URL
# ═════════════════════════════════════════════════════════════════════════


class TestOpenAIConversion:
    async def test_base64_image_to_data_url(self):
        content = [
            {"type": "text", "text": "what's this"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "/9j/4AAQSkZJRg",
                },
            },
        ]
        result = await message_transform(content, model_name="gpt-4o")
        # text 保留
        assert result[0] == {"type": "text", "text": "what's this"}
        # image 转换为 data URL
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/jpeg;base64,/9j/4AAQSkZJRg"

    async def test_url_image_to_image_url(self):
        content = [
            {
                "type": "image",
                "source": {"type": "url", "url": "https://example.com/x.png"},
            }
        ]
        result = await message_transform(content, model_name="gpt-4o")
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "https://example.com/x.png"

    async def test_mixed_blocks(self):
        content = [
            {"type": "text", "text": "page 1 and 2:"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "aaaa",
                },
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "bbbb",
                },
            },
        ]
        result = await message_transform(content, model_name="gpt-4o")
        assert len(result) == 3
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"
        assert "data:image/png;base64,aaaa" in result[1]["image_url"]["url"]
        assert result[2]["type"] == "image_url"
        assert "data:image/png;base64,bbbb" in result[2]["image_url"]["url"]

    async def test_existing_image_url_passthrough(self):
        """用户直接从 TUI 输入的 image_url block 应原样保留"""
        content = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,xxx"},
            }
        ]
        result = await message_transform(content, model_name="gpt-4o")
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "data:image/png;base64,xxx"


# ═════════════════════════════════════════════════════════════════════════
# Bedrock Anthropic:base64 和 document 都原样透传
# ═════════════════════════════════════════════════════════════════════════


class TestModelNameFallback:
    """model_name=None 时应按 LLM_MODEL_NAME env 推断 provider。"""

    async def test_none_model_name_falls_back_to_env_openai(self, monkeypatch):
        """env=gpt-4o + model_name=None → 走 OpenAI 转换"""
        monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4o")
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "abc",
                },
            }
        ]
        result = await message_transform(content, model_name=None)
        assert result[0]["type"] == "image_url"
        assert "data:image/png;base64,abc" in result[0]["image_url"]["url"]

    async def test_none_model_name_falls_back_to_env_anthropic(self, monkeypatch):
        """env=claude + model_name=None → 原样透传"""
        monkeypatch.setenv("LLM_MODEL_NAME", "claude-sonnet-4-5")
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "abc",
                },
            }
        ]
        result = await message_transform(content, model_name=None)
        assert result == content


class TestBedrockPassthrough:
    async def test_base64_image_passthrough(self):
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "aaaa",
                },
            }
        ]
        # Bedrock claude 模型
        result = await message_transform(
            content, model_name="us.anthropic.claude-sonnet-4-20250514-v1:0"
        )
        # base64 原样透传 (Bedrock 不需要 URL 转换)
        assert result[0]["type"] == "image"
        assert result[0]["source"]["data"] == "aaaa"

    async def test_document_block_passthrough_on_bedrock(self):
        """Bedrock Anthropic Claude 支持原生 PDF,应透传"""
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": "JVBERi0x",
                },
            }
        ]
        result = await message_transform(
            content, model_name="us.anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert result[0]["type"] == "document"
        assert result[0]["source"]["data"] == "JVBERi0x"


# ═════════════════════════════════════════════════════════════════════════
# call_model 集成:验证 HumanMessage 多模态被转换
# ═════════════════════════════════════════════════════════════════════════


class TestCallModelIntegration:
    async def test_call_model_transforms_human_message_content(self, monkeypatch):
        """call_model 应在 ainvoke 之前把 HumanMessage list content 过一遍 transform"""
        from langchain_core.messages import AIMessage, HumanMessage

        captured_messages = []

        class FakeChain:
            async def ainvoke(self, payload):
                captured_messages.extend(payload["messages"])
                return AIMessage(content="ok", tool_calls=[])

        def fake_tool_call_chain(*args, **kwargs):
            return FakeChain()

        # patch 依赖
        import lumi.agents.core.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "tool_call_chain", fake_tool_call_chain)

        # 构造一个带 Anthropic 风格 image block 的 HumanMessage
        human_msg = HumanMessage(
            content=[
                {"type": "text", "text": "see image"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aaaa",
                    },
                },
            ]
        )
        state = {
            "messages": [human_msg],
            "iterations": 1,
            "output_schema": None,
        }

        # 构造一个最小 runtime context
        class FakeContext:
            system_prompt = "test"
            model_name = "gpt-4o"  # OpenAI 触发转换
            tools = []

        class FakeRuntime:
            context = FakeContext()

        await nodes_mod.call_model(state, FakeRuntime())

        # 捕获到的 HumanMessage content 应已被转换为 OpenAI 格式
        assert len(captured_messages) >= 1
        captured_human = captured_messages[0]
        assert isinstance(captured_human, HumanMessage)
        assert isinstance(captured_human.content, list)
        # image block 已转换为 image_url
        types = [b.get("type") for b in captured_human.content if isinstance(b, dict)]
        assert "image_url" in types
        # 原始 HumanMessage 不应被就地修改
        assert human_msg.content[1]["type"] == "image"

    async def test_call_model_skips_tool_and_ai_messages(self, monkeypatch):
        """ToolMessage 和 AIMessage 的 content 不应被 transform 动到"""
        from langchain_core.messages import AIMessage, ToolMessage

        captured_messages = []

        class FakeChain:
            async def ainvoke(self, payload):
                captured_messages.extend(payload["messages"])
                return AIMessage(content="ok", tool_calls=[])

        def fake_tool_call_chain(*args, **kwargs):
            return FakeChain()

        import lumi.agents.core.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "tool_call_chain", fake_tool_call_chain)

        tool_msg = ToolMessage(content="read OK", tool_call_id="x", name="read")
        state = {
            "messages": [tool_msg],
            "iterations": 1,
            "output_schema": None,
        }

        class FakeContext:
            system_prompt = "test"
            model_name = "gpt-4o"
            tools = []

        class FakeRuntime:
            context = FakeContext()

        await nodes_mod.call_model(state, FakeRuntime())
        assert captured_messages[0] is tool_msg  # 原样同一对象

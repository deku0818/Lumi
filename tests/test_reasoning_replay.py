"""验证实际请求序列化和 A→B→A 历史往返，不调用线上模型。"""

import json
from copy import deepcopy

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from lumi.models.adapters import DialectChatOpenAI
from lumi.models.manager import create_llm
from lumi.models.reasoning import ORIGIN, prepare_history


def origin(model="A", provider="p", protocol="openai"):
    return (provider, "https://example.com/v1", protocol, model)


def test_responses_switch_cannot_rehydrate_foreign_reasoning():
    llm = create_llm(
        "B",
        protocol="responses",
        compatibility="standard",
        api_key="test",
        base_url="https://example.com/v1",
        use_cache=False,
        use_previous_response_id=True,
    )
    foreign = AIMessage(
        content="answer A",
        response_metadata={ORIGIN: origin("A", protocol="responses"), "id": "resp_A"},
    )
    history = [
        HumanMessage(content="question"),
        foreign,
        HumanMessage(content="continue"),
    ]
    wire = llm._get_request_payload(prepare_history(history, llm.lumi_origin))
    assert "previous_response_id" not in wire
    assert len(wire["input"]) == 3
    assert foreign.response_metadata["id"] == "resp_A"
    same = foreign.model_copy(
        update={"response_metadata": {ORIGIN: llm.lumi_origin, "id": "resp_B"}}
    )
    wire = llm._get_request_payload(
        prepare_history([history[0], same, history[-1]], llm.lumi_origin)
    )
    assert wire["previous_response_id"] == "resp_B"
    assert llm.store is True


@pytest.mark.parametrize("explicit", [None, "openai", "responses"])
def test_explicit_protocol_wins_but_legacy_responses_flag_survives(explicit):
    llm = create_llm(
        "gpt-5.2",
        protocol=explicit or "",
        compatibility="standard",
        api_key="test",
        base_url="https://example.com/v1",
        use_cache=False,
        use_responses_api=True,
    )
    assert llm.use_responses_api is (explicit != "openai")
    assert llm.lumi_origin[2] == ("openai" if explicit == "openai" else "responses")


def test_legacy_keeps_sdk_auto_routing():
    llm = create_llm(
        "gpt-5.2", api_key="test", base_url="https://example.com/v1", use_cache=False
    )
    assert llm.use_responses_api is None


@pytest.mark.parametrize(
    "fields",
    [{}, {"reasoning_content": ""}, {"reasoning_content": "", "reasoning": ""}],
)
def test_required_reasoning_field_uses_placeholder_for_empty_thinking(fields):
    llm = DialectChatOpenAI(
        model="deepseek", api_key="test", lumi_compatibility="deepseek"
    )
    message = AIMessage(
        content="",
        additional_kwargs=fields,
        tool_calls=[{"id": "call_1", "name": "lookup", "args": {}}],
    )
    wire = llm._get_request_payload([message])["messages"][0]
    assert wire["reasoning_content"] == " "
    assert message.additional_kwargs == fields


def test_structured_reasoning_fragments_keep_signature_and_format():
    from lumi.agents.core.meta_message import extract_thinking_content

    llm = DialectChatOpenAI(
        model="A", api_key="test", lumi_origin=origin(), lumi_compatibility="openrouter"
    )
    chunks = []
    for text in ("first ", "second"):
        chunk = llm._convert_chunk_to_generation_chunk(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "",
                            "reasoning_details": [
                                {
                                    "type": "reasoning.text",
                                    "index": 0,
                                    "text": text,
                                    "format": "native-format",
                                    "signature": "signed",
                                }
                            ],
                        },
                    }
                ]
            },
            AIMessageChunk,
        )
        assert extract_thinking_content(chunk.message) == text
        chunks.append(chunk.message)
    message = chunks[0] + chunks[1]
    message.response_metadata[ORIGIN] = origin()
    original = deepcopy(message)
    wire = llm._get_request_payload([message])["messages"][0]
    assert wire["reasoning_details"] == [
        {
            "type": "reasoning.text",
            "index": 0,
            "text": "first second",
            "format": "native-format",
            "signature": "signed",
        }
    ]
    assert message == original
    assert prepare_history([message], origin("B")) == []


def test_projection_keeps_originals_and_recovers_a_after_switch():
    a = AIMessage(
        content=[
            {"type": "thinking", "thinking": "A thought", "signature": "signed"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": "answer"},
        ],
        additional_kwargs={"reasoning_content": "A thought"},
        response_metadata={ORIGIN: origin()},
    )
    b = AIMessage(
        content="B answer",
        additional_kwargs={"reasoning_content": "B thought"},
        response_metadata={ORIGIN: origin("B")},
    )
    history = [HumanMessage(content="question"), a, b]
    original = deepcopy(history)
    switched = prepare_history(history, origin("B"))
    assert switched[1].content == [{"type": "text", "text": "answer"}]
    assert switched[1].additional_kwargs == {}
    assert switched[2] is b
    assert prepare_history(history, origin())[1] is a
    assert history == original
    # checkpoint 序列化后来源标记及原始签名仍可用。
    serde = JsonPlusSerializer()
    restored = serde.loads_typed(serde.dumps_typed(history))
    assert prepare_history(restored, origin())[1] is restored[1]
    assert restored[1].content == a.content
    assert restored[1].additional_kwargs == a.additional_kwargs


def test_same_name_other_provider_and_unsigned_legacy_are_filtered():
    signed = AIMessage(
        content=[
            {
                "type": "reasoning",
                "reasoning": "thought",
                "extras": {"signature": "sig"},
            }
        ],
        additional_kwargs={
            "__gemini_function_call_thought_signatures__": {"call_1": "sig"}
        },
        response_metadata={ORIGIN: origin()},
        tool_calls=[{"name": "test", "args": {}, "id": "call_1"}],
    )
    for target in (origin(provider="other"), origin(protocol="responses")):
        projected = prepare_history([signed], target)[0]
        assert projected.content == []
        assert projected.additional_kwargs == {}
        assert projected.tool_calls == signed.tool_calls
    legacy = signed.model_copy(update={"response_metadata": {}})
    assert prepare_history([legacy], origin())[0].content == []


def test_signature_named_tool_arguments_are_not_thinking_metadata():
    arguments = {
        "signature": "user document signature",
        "nested": {"encrypted_content": "user data"},
    }
    message = AIMessage(
        content=[
            {"type": "tool_use", "id": "call_1", "name": "sign", "input": arguments}
        ],
        tool_calls=[{"name": "sign", "id": "call_1", "args": arguments}],
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "sign", "arguments": json.dumps(arguments)},
                    "extra_content": {
                        "google": {"thought_signature": "model signature"}
                    },
                }
            ]
        },
        response_metadata={ORIGIN: origin()},
    )
    projected = prepare_history([message], origin("B"))[0]
    assert projected.tool_calls[0]["args"] == arguments
    assert projected.content[0]["input"] == arguments
    assert "model signature" not in str(projected.additional_kwargs)
    assert "model signature" in str(message.additional_kwargs)


def test_explicit_profile_protocol_wins_over_model_name():
    from lumi.models import provider_store

    profile = provider_store.upsert(
        {
            "name": "Qwen alias",
            "base_url": "https://example.com/v1",
            "api_key": "test",
            "models": ["claude-alias"],
            "protocol": "openai",
            "compatibility": "qwen",
            "preset": "qwen",
        }
    )
    llm = create_llm("claude-alias", provider=profile.id, use_cache=False)
    assert isinstance(llm, DialectChatOpenAI)
    assert llm.lumi_compatibility == "qwen"
    assert llm.lumi_origin == (profile.id, profile.base_url, "openai", "claude-alias")
    from lumi.gateway.bridge.providers import list_providers

    wire = list_providers()["profiles"][0]
    assert (wire["protocol"], wire["compatibility"], wire["preset"]) == (
        "openai",
        "qwen",
        "qwen",
    )


@pytest.mark.asyncio
async def test_connection_probe_uses_draft_protocol(monkeypatch):
    from lumi.gateway.bridge.providers import test_provider as probe
    from lumi.models import manager

    seen = {}

    class Client:
        async def ainvoke(self, messages):
            return AIMessage(content="pong")

    def create(**kwargs):
        seen.update(kwargs)
        return Client()

    monkeypatch.setattr(manager, "create_llm", create)
    result = await probe(
        "https://example.com", "test", "opaque-alias", "anthropic", "minimax"
    )
    assert result["ok"]
    assert seen["protocol"] == "anthropic"
    assert seen["compatibility"] == "minimax"


def response_body(model):
    return {
        "id": "response-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": f"{model} thought",
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
async def test_real_openai_payload_async_switch_and_sync_roundtrip():
    sent = []

    def handle(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, json=response_body(payload["model"]))

    transport = httpx.MockTransport(handle)
    with httpx.Client(transport=transport) as client:
        async with httpx.AsyncClient(transport=transport) as async_client:

            def model(name):
                return DialectChatOpenAI(
                    model=name,
                    api_key="test",
                    base_url="https://example.com/v1",
                    http_client=client,
                    http_async_client=async_client,
                    lumi_origin=origin(name),
                    lumi_compatibility="qwen",
                )

            a, b = model("A"), model("B")
            user = HumanMessage(content="question")
            first = await a.ainvoke([user])
            assert first.response_metadata[ORIGIN] == origin()
            original = first.model_dump()
            second = await b.ainvoke([user, first, user])
            assert "reasoning_content" not in sent[-1]["messages"][1]
            a.invoke([user, first, user, second, user])
            assert sent[-1]["messages"][1]["reasoning_content"] == "A thought"
            assert "reasoning_content" not in sent[-1]["messages"][3]
            assert first.model_dump() == original


@pytest.mark.asyncio
async def test_streamed_reasoning_survives_tool_loop():
    sent = []

    def handle(request):
        payload = json.loads(request.content)
        sent.append(payload)
        deltas = [
            {"role": "assistant", "reasoning_content": "first "},
            {"reasoning_content": "second"},
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "test", "arguments": "{}"},
                    }
                ]
            },
        ]
        chunks = [
            {
                "id": "r",
                "model": "A",
                "choices": [{"index": 0, "delta": d, "finish_reason": None}],
            }
            for d in deltas
        ]
        chunks.append(
            {
                "id": "r",
                "model": "A",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
            + "data: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = DialectChatOpenAI(
            model="A",
            api_key="test",
            base_url="https://example.com/v1",
            http_async_client=client,
            lumi_origin=origin(),
            lumi_compatibility="deepseek",
            streaming=True,
        )
        user = HumanMessage(content="question")
        reply = await llm.ainvoke([user])
        assert reply.additional_kwargs["reasoning_content"] == "first second"
        assert reply.response_metadata[ORIGIN] == origin()
        await llm.ainvoke(
            [user, reply, ToolMessage(content="done", tool_call_id="call_1")]
        )
        assert sent[-1]["messages"][1]["reasoning_content"] == "first second"
        assert sent[-1]["messages"][1]["tool_calls"][0]["id"] == "call_1"
        full = None
        async for chunk in llm.astream([user]):
            full = chunk if full is None else full + chunk
        assert isinstance(full, AIMessageChunk)
        assert full.response_metadata[ORIGIN] == origin()


@pytest.mark.parametrize(
    "protocol,compatibility",
    [("anthropic", "claude"), ("responses", "standard"), ("gemini", "gemini")],
)
def test_native_thinking_serialization(protocol, compatibility):
    llm = create_llm(
        "alias",
        base_url="https://example.com/v1",
        api_key="test",
        protocol=protocol,
        compatibility=compatibility,
        use_cache=False,
    )
    if protocol == "anthropic":
        blocks = [
            {"type": "thinking", "thinking": "thought", "signature": "signed"},
            {"type": "text", "text": "answer"},
        ]
    elif protocol == "responses":
        blocks = [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "opaque",
            },
            {"type": "text", "text": "answer"},
        ]
    else:
        blocks = [
            {
                "type": "reasoning",
                "reasoning": "thought",
                "extras": {"signature": "c2lnbmVk"},
            },
            {"type": "text", "text": "answer"},
        ]
    message = AIMessage(content=blocks, response_metadata={ORIGIN: llm.lumi_origin})
    history = [HumanMessage(content="q"), message, HumanMessage(content="continue")]
    project = prepare_history(history, llm.lumi_origin)
    payload = (
        llm._prepare_request(project)
        if protocol == "gemini"
        else llm._get_request_payload(project)
    )
    encoded = str(payload)
    assert ("opaque" if protocol == "responses" else "signed") in encoded
    foreign = prepare_history(history, origin("different"))
    payload = (
        llm._prepare_request(foreign)
        if protocol == "gemini"
        else llm._get_request_payload(foreign)
    )
    assert ("opaque" if protocol == "responses" else "signed") not in str(payload)
    assert message.content == blocks

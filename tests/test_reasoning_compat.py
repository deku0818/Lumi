"""兼容接口的真实 SDK 收发路径；HTTP 模拟，不访问线上模型。"""

import json
from copy import deepcopy

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from lumi.agents.core.meta_message import extract_thinking_content
from lumi.models.adapters import DialectChatOpenAI, reasoning_details
from lumi.models.reasoning import DETAIL_DELTAS


@pytest.mark.parametrize("compatibility", ["openrouter", "kimi", "standard"])
@pytest.mark.parametrize("streaming", [False, True])
def test_structured_reasoning_and_parallel_signatures_roundtrip(
    compatibility, streaming
):
    kimi = compatibility == "kimi"
    summary_type = "summary" if kimi else "reasoning.summary"
    encrypted_type = "encrypted" if kimi else "reasoning.encrypted"
    encrypted_field = "encrypted" if kimi else "data"
    fragments = [
        {"type": summary_type, "index": 0, "summary": "First "},
        {"type": summary_type, "index": 0, "summary": "second"},
        {"type": encrypted_type, "index": 1, encrypted_field: "opaque-1"},
        {"type": encrypted_type, "index": 1, encrypted_field: "opaque-2"},
        # 不能跨越加密块合并前面的摘要，即使 type/index 相同。
        {"type": summary_type, "index": 0, "summary": "After"},
    ]
    details = [{**fragments[0], "summary": "First second"}, *fragments[2:]]
    expected = (
        [{k: v for k, v in d.items() if k != "index"} for d in details]
        if kimi
        else details
    )
    calls = [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": "lookup",
                "arguments": json.dumps({"signature": "user data"}),
            },
            "extra_content": {"google": {"thought_signature": f"signed-{i}"}},
        }
        for i in range(2)
    ]
    sent = []

    def handle(request):
        payload = json.loads(request.content)
        sent.append(payload)
        first = len(sent) == 1
        message = {"role": "assistant", "content": "" if first else "answer"}
        if first:
            message["tool_calls"] = calls
            if compatibility != "standard":
                message["reasoning_details"] = details
        finish = "tool_calls" if first else "stop"
        if not payload.get("stream"):
            return httpx.Response(
                200,
                json={
                    "id": "r",
                    "model": payload["model"],
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                },
            )
        deltas = [message]
        if first:
            deltas = [{"role": "assistant", "content": ""}]
            if compatibility != "standard":
                deltas += [{"reasoning_details": [d]} for d in fragments]
            deltas += [
                {
                    "tool_calls": [
                        {
                            **{k: v for k, v in call.items() if k != "extra_content"},
                            "index": i,
                        }
                        for i, call in enumerate(calls)
                    ]
                }
            ]
            # 签名晚于 ID 到达、逆序出现且重复；不能错配或拼接签名字符串。
            deltas += [
                {
                    "tool_calls": [
                        {"index": i, "extra_content": calls[i]["extra_content"]}
                    ]
                }
                for i in (1, 0, 1)
            ]
        chunks = [
            {
                "id": "r",
                "model": payload["model"],
                "choices": [{"index": 0, "delta": d, "finish_reason": None}],
            }
            for d in deltas
        ]
        chunks.append(
            {
                "id": "r",
                "model": payload["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            }
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
            + "data: [DONE]\n\n",
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:

        def model(name):
            return DialectChatOpenAI(
                model=name,
                api_key="test",
                base_url="https://example.com/v1",
                http_client=client,
                streaming=streaming,
                use_responses_api=False,
                lumi_compatibility=compatibility,
                lumi_origin=("provider", "https://example.com/v1", "openai", name),
            )

        a, b = model("A"), model("B")
        user = HumanMessage(content="question")
        response = a.invoke([user])
        serde = JsonPlusSerializer()
        response = serde.loads_typed(serde.dumps_typed(response))
        if compatibility != "standard":
            assert extract_thinking_content(response) == "First secondAfter"
        history = [
            user,
            response,
            *[ToolMessage(content="result", tool_call_id=c["id"]) for c in calls],
        ]
        original = deepcopy(history)
        answer_a = a.invoke(history)
        replay = sent[-1]["messages"][1]
        assert replay["tool_calls"] == calls
        if compatibility != "standard":
            assert replay["reasoning_details"] == expected
        if kimi:
            assert "reasoning_content" in replay

        history += [answer_a, HumanMessage(content="switch")]
        answer_b = b.invoke(history)
        switched = sent[-1]["messages"][1]
        assert "reasoning_details" not in switched
        assert all("extra_content" not in c for c in switched["tool_calls"])
        assert [c["id"] for c in switched["tool_calls"]] == [c["id"] for c in calls]
        assert all(
            json.loads(c["function"]["arguments"])["signature"] == "user data"
            for c in switched["tool_calls"]
        )
        a.invoke([*history, answer_b, HumanMessage(content="switch back")])
        assert sent[-1]["messages"][1] == replay
        assert history[: len(original)] == original


@pytest.mark.parametrize("key", ["id", "index", "format", "signature"])
def test_reasoning_deltas_keep_distinct_identities(key):
    first = {
        "type": "reasoning.text",
        "index": 0,
        "text": "one",
        key: 1 if key == "index" else "a",
    }
    second = {**first, "text": "two", key: 2 if key == "index" else "b"}
    fields = {DETAIL_DELTAS: [[first], [second]]}
    original = deepcopy(fields)
    assert reasoning_details(fields) == [first, second]
    assert fields == original


def test_reasoning_identity_arriving_late_is_preserved():
    fields = {
        DETAIL_DELTAS: [
            [{"type": "reasoning.summary", "summary": "one", "signature": ""}],
            [
                {
                    "type": "reasoning.summary",
                    "summary": "two",
                    "id": "r",
                    "signature": "signed",
                }
            ],
            [
                {
                    "type": "reasoning.summary",
                    "summary": "three",
                    "id": None,
                    "signature": "",
                }
            ],
        ]
    }
    assert reasoning_details(fields) == [
        {
            "type": "reasoning.summary",
            "summary": "onetwothree",
            "id": "r",
            "signature": "signed",
        }
    ]
    # 非流式完整块即使相邻也不应当作增量合并；list content 不阻止摘要显示。
    details = [
        {"type": "reasoning.summary", "summary": "one"},
        {"type": "reasoning.summary", "summary": "two"},
    ]
    assert reasoning_details({"reasoning_details": details}) == details
    assert (
        extract_thinking_content(
            AIMessage(content=[], additional_kwargs={"reasoning_details": details})
        )
        == "onetwo"
    )

"""验证缓存绑定、原生/兼容请求格式和历史不变；不调用线上服务。"""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from lumi.models import chain as chains
from lumi.models.adapters import DialectChatOpenAI, ReplayChatAnthropic
from lumi.models.cache import CACHE_CONTROL
from lumi.models.reasoning import ORIGIN


def markers(value):
    if isinstance(value, list):
        return sum(markers(v) for v in value)
    if isinstance(value, dict):
        return int("cache_control" in value) + sum(markers(v) for v in value.values())
    return 0


@pytest.mark.parametrize(
    "endpoint,automatic",
    [
        ("https://api.anthropic.com", True),
        ("https://proxy.example/anthropic", False),
        ("https://api.minimaxi.com/anthropic", False),
    ],
)
def test_chain_cache_moves_without_mutating_history(monkeypatch, endpoint, automatic):
    llm = ReplayChatAnthropic(
        model="claude-sonnet-4-6",
        api_key="test",
        base_url=endpoint,
        lumi_origin=("p", endpoint, "anthropic", "claude-sonnet-4-6"),
    )
    sent = []

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        sent.append(self._get_request_payload(messages, stop=stop, **kwargs))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="answer"))]
        )

    monkeypatch.setattr(ReplayChatAnthropic, "_generate", generate)
    monkeypatch.setattr(chains, "create_llm", lambda **kw: llm)
    chain = chains.tool_call_chain(
        [], system_prompt="stable instructions", streaming=False
    )
    history = [
        HumanMessage(content="question"),
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "thought", "signature": "signed"}
            ],
            tool_calls=[{"name": "lookup", "args": {}, "id": "call_1"}],
            response_metadata={ORIGIN: llm.lumi_origin},
        ),
        ToolMessage(content="result", tool_call_id="call_1"),
    ]
    original = deepcopy(history)
    answer = chain.invoke({"messages": history})
    chain.invoke(
        {"messages": [*history, answer, HumanMessage(content="next question")]}
    )
    assert history == original
    assert sent[0]["system"] == sent[1]["system"]
    assert (
        sent[0]["system"][0]["cache_control"]
        == CACHE_CONTROL
        == {"type": "ephemeral", "ttl": "1h"}
    )
    for payload in sent:
        assert payload["messages"][1]["content"][0] == original[1].content[0]
        if automatic:
            assert payload["cache_control"] == CACHE_CONTROL
            assert markers(payload["messages"]) == 0
        else:
            assert "cache_control" not in payload
            assert markers(payload["messages"]) == 1
            assert (
                payload["messages"][-1]["content"][-1]["cache_control"] == CACHE_CONTROL
            )
    if not automatic:
        assert sent[0]["messages"][-1]["content"][-1]["type"] == "tool_result"
        assert sent[1]["messages"][-1]["content"][-1]["type"] == "text"


def test_summary_keeps_only_fixed_system_breakpoint(monkeypatch):
    llm = ReplayChatAnthropic(model="claude-sonnet-4-6", api_key="test")
    sent = []

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        sent.append(self._get_request_payload(messages, **kwargs))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="summary"))]
        )

    monkeypatch.setattr(ReplayChatAnthropic, "_generate", generate)
    monkeypatch.setattr(chains, "create_llm", lambda **kw: llm)
    chain = chains.tool_call_chain(
        [], system_prompt="stable", cache_history=False, streaming=False
    )
    chain.invoke({"messages": [HumanMessage(content="summarize")]})
    assert "cache_control" not in sent[0]
    assert markers(sent[0]["system"]) == 1
    assert markers(sent[0]["messages"]) == 0


def test_compatible_endpoint_from_environment_uses_block_cache(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example/anthropic")
    llm = ReplayChatAnthropic(model="claude-sonnet-4-6", api_key="test")
    payload = llm._get_request_payload(
        [HumanMessage(content="hello")], cache_control=CACHE_CONTROL
    )
    assert "cache_control" not in payload
    assert payload["messages"][0]["content"][0]["cache_control"] == CACHE_CONTROL


def test_non_anthropic_chain_has_no_anthropic_cache_markers(monkeypatch):
    llm = DialectChatOpenAI(model="example", api_key="test")
    sent = []

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        sent.append(self._get_request_payload(messages, **kwargs))
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="answer"))]
        )

    monkeypatch.setattr(DialectChatOpenAI, "_generate", generate)
    monkeypatch.setattr(chains, "create_llm", lambda **kw: llm)
    chains.tool_call_chain([], system_prompt="stable", streaming=False).invoke(
        {"messages": [HumanMessage(content="q")]}
    )
    assert markers(sent[0]) == 0

"""整图级「终止本轮」语义：hook 的 Block、结构化输出连续失败上限，都必须真的结束本轮。

ToolExecutor 挂着条件边 after_tool_executor，节点自己返回的 Command(goto=END) 会与条件边
取并集——END 被 CallModel 盖过，Block 结束不了本轮、结构化输出失败会一直烧到递归上限。
PreprocessMessages 是固定边，UserPromptSubmit 的 Block 同样挡不住模型调用。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conftest import resolved
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from lumi.agents.core import nodes
from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.hooks import Block, replace_hooks
from lumi.agents.core.state import LumiAgentContext
from lumi.agents.core.structured_tool import (
    MAX_CONSECUTIVE_FAILURES,
    STRUCTURED_OUTPUT_TOOL_NAME,
)

_CONFIG = SimpleNamespace(
    config=SimpleNamespace(
        agents=SimpleNamespace(max_tokens=None),
        token=SimpleNamespace(
            context_length=100_000,
            summary_threshold=0.9,
            summary_failure_circuit_threshold=3,
            summary_circuit_reset_seconds=60,
        ),
    ),
)


async def _run(chain, state: dict) -> dict:
    with (
        patch.object(nodes, "tool_call_chain", return_value=chain),
        patch.object(nodes, "get_config", return_value=_CONFIG),
        patch.object(nodes, "detect_protocol", return_value="openai"),
        patch.object(nodes, "resolve", return_value=resolved(0)),
    ):
        return await LumiAgent().graph.ainvoke(
            state,
            {"recursion_limit": 40},
            context=LumiAgentContext(model_name="fake-model", tool_mode="privileged"),
        )


def _tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


async def test_pre_tool_use_block_ends_turn():
    chain = SimpleNamespace(
        ainvoke=AsyncMock(return_value=_tool_call("read", {"file_path": "x"}, "c1"))
    )

    async def block(ctx):
        return Block("策略禁止")

    with replace_hooks("PreToolUse", [block]):
        result = await _run(chain, {"messages": [HumanMessage(content="读 x")]})

    assert chain.ainvoke.await_count == 1  # 拦下后不再回模型
    last_two = result["messages"][-2:]
    assert isinstance(last_two[0], ToolMessage) and last_two[0].status == "error"
    assert last_two[1].content == "策略禁止"


async def test_structured_output_failures_stop_at_limit():
    calls = iter(range(100))

    def bad(*args, **kwargs) -> AIMessage:
        return _tool_call(STRUCTURED_OUTPUT_TOOL_NAME, {"wrong": 1}, f"s{next(calls)}")

    chain = SimpleNamespace(ainvoke=AsyncMock(side_effect=bad))
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    result = await _run(
        chain, {"messages": [HumanMessage(content="q")], "output_schema": schema}
    )

    assert chain.ainvoke.await_count == MAX_CONSECUTIVE_FAILURES
    assert isinstance(result["messages"][-1], AIMessage)  # 以中止说明收尾


async def test_user_prompt_submit_block_skips_model():
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="答")))

    async def block(ctx):
        return Block("该提问被拦截")

    with replace_hooks("UserPromptSubmit", [block]):
        result = await _run(chain, {"messages": [HumanMessage(content="q")]})

    assert chain.ainvoke.await_count == 0
    assert result["messages"][-1].content == "该提问被拦截"

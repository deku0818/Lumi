"""子代理（无 checkpointer 的 LumiAgent）在父运行里执行时，不借用父会话的 checkpointer。

LangGraph 里 ``compile(checkpointer=None)`` 的语义是「作为子图继承父级」：agent 工具、
workflow、dream 在父图的工具节点里 ainvoke 子图，子图每个超步都会写进父会话 thread；
后台子代理比父 bridge 活得久时，父连接一关它就 ProgrammingError。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conftest import resolved
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from lumi.agents.core import nodes
from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.state import LumiAgentContext

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


async def test_subagent_does_not_write_into_parent_thread():
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="done")))
    child = LumiAgent()  # 子代理：不传 checkpointer

    async def run_child(state: MessagesState) -> dict:
        await child.graph.ainvoke(
            {"messages": [HumanMessage(content="sub task")]},
            context=LumiAgentContext(model_name="fake-model"),
        )
        return {}

    parent_saver = InMemorySaver()
    builder = StateGraph(MessagesState)
    builder.add_node("tool", run_child)
    builder.add_edge(START, "tool")
    builder.add_edge("tool", END)
    parent = builder.compile(checkpointer=parent_saver)

    with (
        patch.object(nodes, "tool_call_chain", return_value=chain),
        patch.object(nodes, "get_config", return_value=_CONFIG),
        patch.object(nodes, "detect_protocol", return_value="openai"),
        patch.object(nodes, "resolve", return_value=resolved(0)),
    ):
        await parent.ainvoke(
            {"messages": [HumanMessage(content="hi")]},
            {"configurable": {"thread_id": "parent"}},
        )

    assert chain.ainvoke.await_count == 1  # 子代理确实跑完了一轮
    namespaces = {
        cp.config["configurable"]["checkpoint_ns"]
        async for cp in parent_saver.alist({"configurable": {"thread_id": "parent"}})
    }
    assert namespaces == {""}  # 父 thread 里只有父图自己的 checkpoint

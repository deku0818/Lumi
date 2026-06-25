"""守护 compaction 流隔离所依赖的 LangGraph 不变量。

bridge 靠 ``event.metadata.langgraph_node == "Summarizer"`` 把压缩节点内部的摘要
LLM 调用拦下来（转成 compaction.status，不外泄为 message.*）。本测试锁住该不变量：
节点内 chat model 的 ``on_chat_model_*`` 事件确实携带 ``langgraph_node`` 标识其所属
节点——LangGraph 若改这点，过滤会静默失效、摘要全文重新泄漏成助手回答。
"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph


class _S(TypedDict):
    x: int


async def test_node_chat_model_events_carry_langgraph_node():
    model = FakeListChatModel(responses=["这是摘要SECRET"])

    async def summarizer(s: _S) -> _S:
        await model.ainvoke([HumanMessage(content="总结")])
        return {"x": s["x"] + 1}

    g = StateGraph(_S)
    g.add_node("Summarizer", summarizer)
    g.add_edge(START, "Summarizer")
    g.add_edge("Summarizer", END)
    graph = g.compile()

    chat_events: list[tuple[str, str | None, str | None]] = []
    async for ev in graph.astream_events({"x": 0}, version="v2"):
        kind = ev.get("event", "")
        if kind.startswith("on_chat_model"):
            node = ev.get("metadata", {}).get("langgraph_node")
            chunk = ev.get("data", {}).get("chunk")
            text = getattr(chunk, "content", None) if chunk is not None else None
            chat_events.append((kind, node, text))

    kinds = {k for k, _, _ in chat_events}
    # 节点内 ainvoke 仍浮现 stream 事件（astream_events 会逐字浮现）——这正是泄漏来源
    assert "on_chat_model_stream" in kinds
    # 每个 chat model 事件都带 langgraph_node == 'Summarizer'，过滤据此生效
    assert chat_events, "应至少有一个 on_chat_model_* 事件"
    assert all(node == "Summarizer" for _, node, _ in chat_events)
    # 摘要文本确实出现在 stream chunk 里（若不拦截即被当作助手输出）
    streamed = "".join(
        t for k, _, t in chat_events if k == "on_chat_model_stream" and t
    )
    assert "SECRET" in streamed

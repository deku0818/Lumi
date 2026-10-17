"""守护 compaction 流隔离所依赖的 LangGraph 不变量。

bridge 靠 ``event.metadata.langgraph_node == "Compact"`` 把压缩节点内部的摘要
LLM 调用拦下来（转成 compaction.status，不外泄为 message.*）。本测试锁住该不变量：
节点内 chat model 的 ``on_chat_model_*`` 事件确实携带 ``langgraph_node`` 标识其所属
节点——LangGraph 若改这点，过滤会静默失效、摘要全文重新泄漏成助手回答。
"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from lumi.gateway.bridge import AgentBridge


class _S(TypedDict):
    x: int


async def test_node_chat_model_events_carry_langgraph_node():
    model = FakeListChatModel(responses=["这是摘要SECRET"])

    async def compact(s: _S) -> _S:
        await model.ainvoke([HumanMessage(content="总结")])
        return {"x": s["x"] + 1}

    g = StateGraph(_S)
    g.add_node("Compact", compact)
    g.add_edge(START, "Compact")
    g.add_edge("Compact", END)
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
    # 每个 chat model 事件都带 langgraph_node == 'Compact'，过滤据此生效
    assert chat_events, "应至少有一个 on_chat_model_* 事件"
    assert all(node == "Compact" for _, node, _ in chat_events)
    # 摘要文本确实出现在 stream chunk 里（若不拦截即被当作助手输出）
    streamed = "".join(
        t for k, _, t in chat_events if k == "on_chat_model_stream" and t
    )
    assert "SECRET" in streamed


def test_bridge_hides_compact_stream():
    node_name = "Compact"
    bridge = AgentBridge()
    for kind, active in [("start", True), ("end", False), ("error", False)]:
        events = list(
            bridge._translate_event(
                {
                    "event": f"on_chat_model_{kind}",
                    "metadata": {"langgraph_node": node_name},
                }
            )
        )
        assert len(events) == 1
        assert events[0].kind == "compaction.status"
        assert events[0].data == {"active": active}
    assert not list(
        bridge._translate_event(
            {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": node_name},
                "data": {"chunk": "内部压缩内容"},
            }
        )
    )

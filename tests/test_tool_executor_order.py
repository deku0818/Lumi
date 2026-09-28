"""tool_executor 写回的消息顺序：同批 tool_result 必须紧跟 tool_use。

read 读图片/PDF 返回 Command，里面是 [ToolMessage, 携带图片的 HumanMessage]。逐个
Command 原样应用时，同批其它工具的 ToolMessage 会排到那条 HumanMessage 之后，
provider 视为 tool_use 缺 tool_result，整条会话从此每次调用都报错。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from lumi.agents.core import nodes
from lumi.agents.core.state import LumiAgentContext, LumiAgentState


class _BatchToolNode:
    """ToolNode 在「任一工具返回 Command」时的真实输出形态（_combine_tool_outputs）。"""

    def __init__(self, tools, handle_tool_errors=None):
        pass

    async def ainvoke(self, calls, config=None):
        image = HumanMessage(content=[{"type": "text", "text": "图片内容"}])
        return [
            Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="已读取图片", tool_call_id="r", name="read"
                        ),
                        image,
                    ]
                }
            ),
            {"messages": [ToolMessage(content="ok", tool_call_id="b", name="bash")]},
        ]


async def test_tool_results_precede_injected_media(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _BatchToolNode)
    builder = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    builder.add_node("ToolExecutor", nodes.tool_executor)
    builder.add_edge(START, "ToolExecutor")
    builder.add_edge("ToolExecutor", END)
    graph = builder.compile()

    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "read", "args": {"file_path": "a.png"}, "id": "r"},
            {"name": "bash", "args": {"command": "ls"}, "id": "b"},
        ],
    )
    result = await graph.ainvoke({"messages": [ai]}, context=LumiAgentContext())
    kinds = [type(m).__name__ for m in result["messages"]]
    assert kinds == ["AIMessage", "ToolMessage", "ToolMessage", "HumanMessage"]

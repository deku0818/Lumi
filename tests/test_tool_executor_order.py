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


async def test_post_tool_hooks_run_when_batch_contains_a_command(monkeypatch):
    # 回归：同批任一工具返回 Command（todos / read 图片等）时曾整批跳过 PostToolUse——
    # 用户配置的审计 / 格式化 hook 随同批工具不同而随机失效
    from lumi.agents.core.hooks import AdditionalContext, replace_hooks

    monkeypatch.setattr(nodes, "ToolNode", _BatchToolNode)
    seen: list[list[str]] = []

    async def post_hook(ctx):
        seen.append([tc["name"] for tc in ctx.payload["tool_calls"]])
        return AdditionalContext("AUDIT")

    builder = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    builder.add_node("ToolExecutor", nodes.tool_executor)
    builder.add_edge(START, "ToolExecutor")
    builder.add_edge("ToolExecutor", END)
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "read", "args": {"file_path": "a.png"}, "id": "r"},
            {"name": "bash", "args": {"command": "ls"}, "id": "b"},
        ],
    )
    with replace_hooks("PostToolUse", [post_hook]):
        result = await builder.compile().ainvoke(
            {"messages": [ai]}, context=LumiAgentContext()
        )
    assert seen == [["read", "bash"]]
    kinds = [type(m).__name__ for m in result["messages"]]
    assert kinds[:3] == ["AIMessage", "ToolMessage", "ToolMessage"]


async def test_structured_output_abort_counts_batches_with_commands(monkeypatch):
    # 回归：结构化输出连续失败上限曾在「同批带 Command 工具」时被跳过，永不终止
    from lumi.agents.core.structured_tool import (
        MAX_CONSECUTIVE_FAILURES,
        STRUCTURED_OUTPUT_TOOL_NAME,
    )

    class _FailingBatch:
        def __init__(self, tools, handle_tool_errors=None):
            pass

        async def ainvoke(self, calls, config=None):
            bad = ToolMessage(
                content="校验失败",
                tool_call_id="s",
                name=STRUCTURED_OUTPUT_TOOL_NAME,
                status="error",
            )
            todo = ToolMessage(content="ok", tool_call_id="t", name="todos")
            return [
                Command(update={"messages": [todo], "todos": []}),
                {"messages": [bad]},
            ]

    monkeypatch.setattr(nodes, "ToolNode", _FailingBatch)
    prior = [
        ToolMessage(
            content="校验失败",
            tool_call_id=f"p{i}",
            name=STRUCTURED_OUTPUT_TOOL_NAME,
            status="error",
        )
        for i in range(MAX_CONSECUTIVE_FAILURES - 1)
    ]
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": STRUCTURED_OUTPUT_TOOL_NAME, "args": {}, "id": "s"},
            {"name": "todos", "args": {"todos": []}, "id": "t"},
        ],
    )
    state = {
        "messages": [HumanMessage("q"), *prior, ai],
        "output_schema": {"type": "object", "properties": {}},
    }
    out = await nodes.tool_executor(state, _Runtime(), {})
    updates = out if isinstance(out, list) else [out]
    assert any(
        (u.update if isinstance(u, Command) else u).get("tool_cancelled")
        for u in updates
    )


class _Runtime:
    context = LumiAgentContext()

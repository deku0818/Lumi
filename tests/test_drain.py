"""协作式停机（drain）的锁定测试。

drain 走了条绕路：``astream_events(version="v2")`` 不转发 ``control=``，于是改从
config 注入带 control 的 parent runtime，用到 LangGraph 私有常量
``CONFIG_KEY_RUNTIME``（见 ``lumi/agents/core/run_control.py``）。这条路一旦被上游
改掉，drain 会**静默失效**——停机时没人会注意到少了一次优雅退出。所以这里用真实
LumiAgent 图把它锁死。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import graph_run_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphDrained
from langgraph.runtime import RunControl

from lumi.agents.core import graph as graph_module
from lumi.agents.core import nodes
from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.run_control import (
    _active,
    drain_all,
    register,
    unregister,
    with_run_control,
)
from lumi.agents.core.state import LumiAgentContext


@pytest.mark.asyncio
async def test_drain_stops_real_graph_at_superstep_boundary():
    """真实图 + astream_events(v2)：drain 请求后停在边界、抛 GraphDrained，
    checkpoint 完整且 next 指向待执行节点，续跑能接上。

    这条红了通常意味着 CONFIG_KEY_RUNTIME 这条注入路径被上游改了。
    """
    control = RunControl()
    calls = {"n": 0}

    async def slow_model(state, runtime):
        calls["n"] += 1
        if calls["n"] == 1:
            control.request_drain("test")  # 第一步跑完就请求停机
            return {
                "messages": [
                    AIMessage(
                        content="",
                        id=f"a{calls['n']}",
                        tool_calls=[{"name": "noop", "args": {}, "id": "tc1"}],
                    )
                ],
                "iterations": 2,
            }
        return {"messages": [AIMessage("done", id="final")], "iterations": 3}

    checkpointer = MemorySaver()
    config = {"configurable": {"thread_id": "t-drain"}}
    context = LumiAgentContext(model_name="fake")

    with (
        patch.object(nodes, "get_config", return_value=graph_run_config()),
        patch.object(graph_module, "call_model", slow_model),
    ):
        graph = LumiAgent(checkpointer=checkpointer).graph
        with pytest.raises(GraphDrained):
            async for _ in graph.astream_events(
                {"messages": [HumanMessage("hi", id="h1")], "iterations": 1},
                with_run_control(config, control),
                context=context,
                version="v2",
            ):
                pass

        snapshot = await graph.aget_state(config)
        assert snapshot.next, "drain 应停在边界，next 指向待执行节点"
        assert calls["n"] == 1  # 第二次模型调用没发生


@pytest.mark.asyncio
async def test_with_run_control_keeps_context_and_original_config():
    """注入 control 不能吃掉 context，也不能改原 config（同一 bridge 复用它）。"""
    seen = {}

    async def spy(state, runtime):
        seen["model"] = runtime.context.model_name
        return {"messages": [AIMessage("ok", id="r1")], "iterations": 2}

    control = RunControl()
    config = {"configurable": {"thread_id": "t-ctx"}}
    injected = with_run_control(config, control)
    assert config == {"configurable": {"thread_id": "t-ctx"}}  # 原 config 未被改

    with (
        patch.object(nodes, "get_config", return_value=graph_run_config()),
        patch.object(graph_module, "call_model", spy),
    ):
        graph = LumiAgent(checkpointer=MemorySaver()).graph
        await graph.ainvoke(
            {"messages": [HumanMessage("hi", id="h1")], "iterations": 1},
            injected,
            context=LumiAgentContext(model_name="fake-model"),
        )
    assert seen["model"] == "fake-model"


def test_drain_all_signals_registered_runs_only():
    a, b = RunControl(), RunControl()
    register(a)
    register(b)
    unregister(b)
    try:
        assert drain_all("bye") == 1
        assert a.drain_requested and not b.drain_requested
    finally:
        _active.clear()

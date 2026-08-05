"""OfflineFlush 离线写回锚点的真实图契约测试。

教训来源：bridge 曾用 aupdate_state(as_node="CallModel") 写回中断半截回复，
玩具图测试全绿，真实图上却因 is_use_tool 条件边要求 Runtime 注入而必抛
ValueError 且被吞——玩具图锁不住真实图的条件边形状。此文件用真实 LumiAgent
图锁两件事：OfflineFlush 锚点可写且写完 next 为空；CallModel 依旧不可写
（哪天不抛了，说明 LangGraph 行为变化，锚点绕道可以重新评估）。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from lumi.agents.core.graph import LumiAgent


@pytest.fixture
def graph():
    return LumiAgent(checkpointer=MemorySaver()).graph


@pytest.mark.asyncio
async def test_offline_flush_writes_and_leaves_clean_state(graph):
    cfg = {"configurable": {"thread_id": "t1"}}
    await graph.aupdate_state(
        cfg, {"messages": [HumanMessage("hi")]}, as_node="OfflineFlush"
    )
    await graph.aupdate_state(
        cfg,
        {
            "messages": [
                AIMessage(
                    content="半截回复",
                    additional_kwargs={"lumi": {"interrupted": True}},
                )
            ]
        },
        as_node="OfflineFlush",
    )
    state = await graph.aget_state(cfg)
    assert state.next == ()  # 出边直达 END：写完即干净，无 stale 残留
    tail = state.values["messages"][-1]
    assert tail.content == "半截回复"
    assert tail.additional_kwargs["lumi"]["interrupted"] is True


def test_bridge_referenced_node_names_exist(graph):
    """bridge 侧字面量引用的节点名必须存在于真实图——改名时此处先红。"""
    assert {"CallModel", "Summarizer", "ToolExecutor", "OfflineFlush"} <= set(
        graph.nodes
    )


@pytest.mark.asyncio
async def test_call_model_as_node_still_unwritable(graph):
    """锚点存在的理由：CallModel 出边需要 Runtime，aupdate_state 无法注入。
    此断言若有天失败（不再抛错），说明 LangGraph 已支持，可评估撤销绕道。"""
    cfg = {"configurable": {"thread_id": "t2"}}
    await graph.aupdate_state(
        cfg, {"messages": [HumanMessage("hi")]}, as_node="OfflineFlush"
    )
    with pytest.raises(ValueError, match="is_use_tool"):
        await graph.aupdate_state(
            cfg, {"messages": [AIMessage("x")]}, as_node="CallModel"
        )

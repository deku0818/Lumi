"""离线写回的两条约定由 ``AgentBridge.flush_offline`` 这一个出口保证。

- 补消息 id：messages 通道是 DeltaChannel，``aupdate_state`` 不像节点写入那样自动
  补 id（见 ``stamp_missing_ids``）。漏补是**静默**的——消息一辈子没 id，而 rewind
  截断 / 半截判重 / 压缩选材全按 id 认消息。
- 挂 ``OfflineFlush`` 锚点：写完 ``next`` 即空，不派生任何任务。

分散在各调用点「记得调」是先前的形态；这里锁的是出口本身，新增写回路径只要走它
就自动满足，不必再为每条路径各写一个用例。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from toy_graph import bridge_with

from lumi.agents.core.graph import LumiAgent


@pytest.mark.asyncio
async def test_flush_offline_stamps_ids_and_leaves_checkpoint_clean():
    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-flush"}}
    bridge = bridge_with(config, graph)

    # 全部不带 id：生产里 synthetic_human_message / 半截回复 / 补合成 ToolMessage
    # 构造出来就是这样
    await bridge.flush_offline(
        {"messages": [HumanMessage("没有 id 的提问"), AIMessage("没有 id 的回答")]}
    )

    snapshot = await graph.aget_state(config)
    messages = snapshot.values["messages"]
    assert [m.content for m in messages] == ["没有 id 的提问", "没有 id 的回答"]
    assert all(m.id for m in messages), "离线写回必须补上 id"
    assert not snapshot.next, "OfflineFlush 写完不该留待执行节点"


@pytest.mark.asyncio
async def test_flush_offline_stamps_inside_overwrite():
    """压缩走的是 ``Overwrite`` 包装，补 id 要能穿透它。"""
    from lumi.agents.core.preprocessing.compact import build_compacted_update

    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-flush-ow"}}
    bridge = bridge_with(config, graph)
    await bridge.flush_offline({"messages": [HumanMessage("旧历史", id="h1")]})

    snapshot = await graph.aget_state(config)
    await bridge.flush_offline(
        build_compacted_update(snapshot.values["messages"], [], "摘要")
    )

    # [carrier, 原样重挂的那条未被回答的提问]——后者由 find_pending_human 保住
    messages = (await graph.aget_state(config)).values["messages"]
    assert "摘要" in messages[0].content
    assert messages[-1].content == "旧历史"
    assert all(m.id for m in messages), "carrier 是合成消息、构造时没 id，出口要补上"

"""messages 通道（DeltaChannel + 批量 reducer）的语义锁定。

换掉 ``add_messages`` 换来的是 checkpoint 体积（实测 34x），代价是这条通道不再有
``add_messages`` 的全部行为。本文件锁住 Lumi 真正依赖的那几条，任何一条变了都要
在这里先变红：

- 同 id 替换（``persist_partial_reply`` 的承重墙：半截回复被全文原地顶掉）
- ``RemoveMessage`` 删除（rewind 截断）
- ``Overwrite`` 整体替换（压缩写回）
- 节点写入自动补 id（LangGraph 的 put_writes 负责）
- ``aupdate_state`` **不**自动补 id → 离线写回必须自己 stamp（Lumi 的 contract）
"""

from __future__ import annotations

from typing import Annotated, TypedDict

import pytest
from conftest import apply_messages_update, messages_channel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import Overwrite

from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.node_helpers.messages import stamp_missing_ids


def test_messages_channel_is_delta():
    """通道类型本身锁死——退回 add_messages 会让 checkpoint 体积悄悄涨回去。"""
    assert isinstance(messages_channel(), DeltaChannel)


def test_same_id_write_replaces_in_place():
    """承重墙：同 id 写入是原地**替换**而非追加（persist_partial_reply 据此防写重）。"""
    merged = apply_messages_update(
        [HumanMessage("q", id="h1"), AIMessage("半截", id="a1")],
        [AIMessage("全文", id="a1")],
    )
    assert [(m.content, m.id) for m in merged] == [("q", "h1"), ("全文", "a1")]


def test_remove_message_deletes():
    merged = apply_messages_update(
        [HumanMessage("q", id="h1"), AIMessage("a", id="a1")],
        [RemoveMessage(id="a1")],
    )
    assert [m.id for m in merged] == ["h1"]


def test_overwrite_replaces_whole_history():
    merged = apply_messages_update(
        [HumanMessage("q", id="h1"), AIMessage("a", id="a1")],
        Overwrite(value=[HumanMessage("<summary>", id="s1")]),
    )
    assert [m.id for m in merged] == ["s1"]


@pytest.mark.asyncio
async def test_node_writes_get_ids_assigned():
    """节点写入的 id=None 消息由 LangGraph 在 put_writes 补 id（Lumi 不必插手）。"""
    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-delta-node"}}
    await graph.aupdate_state(
        config,
        stamp_missing_ids({"messages": [HumanMessage("h")]}),
        as_node="OfflineFlush",
    )
    snapshot = (await graph.aget_state(config)).values["messages"]
    assert all(m.id for m in snapshot)


@pytest.mark.asyncio
async def test_aupdate_state_does_not_assign_ids_so_lumi_must_stamp():
    """DeltaChannel 的 id 补全只在 put_writes 发生，aupdate_state 不经那条路。

    这条是**反向锁**：哪天 LangGraph 把 aupdate_state 也补上了，本测试会红，
    届时 stamp_missing_ids 就可以删掉，而不是留着当不知所以的祖传代码。
    """
    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-delta-raw"}}
    await graph.aupdate_state(
        config, {"messages": [HumanMessage("没有 id")]}, as_node="OfflineFlush"
    )
    snapshot = (await graph.aget_state(config)).values["messages"]
    assert snapshot[0].id is None, "LangGraph 已自行补 id，stamp_missing_ids 可以删了"


@pytest.mark.asyncio
async def test_reads_history_written_by_the_old_add_messages_channel(tmp_path):
    """存量 checkpoint（``add_messages`` 时代写的）换成 DeltaChannel 后原样读得回来。

    这是整次通道迁移的核心保证：线上 checkpoint 库里全是旧通道写的历史，读不回来
    就是所有会话集体失忆。用同一个库先以旧通道写、再以现行 state 读。
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph

    db = str(tmp_path / "legacy.db")
    config = {"configurable": {"thread_id": "legacy"}}
    history = [HumanMessage("旧问题", id="h1"), AIMessage("旧回答", id="a1")]

    class LegacyState(TypedDict):
        messages: Annotated[list, add_messages]

    legacy = StateGraph(LegacyState)
    legacy.add_node("N", lambda _s: {})
    legacy.add_edge(START, "N")
    legacy.add_edge("N", END)

    conn = await aiosqlite.connect(db)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    await legacy.compile(checkpointer=saver).ainvoke({"messages": history}, config)
    await conn.close()

    conn = await aiosqlite.connect(db)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    graph = LumiAgent(checkpointer=saver).graph
    restored = (await graph.aget_state(config)).values["messages"]
    await conn.close()

    assert [(m.content, m.id) for m in restored] == [("旧问题", "h1"), ("旧回答", "a1")]

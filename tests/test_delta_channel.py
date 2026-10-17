"""messages 通道（DeltaChannel + 批量 reducer）的语义锁定。

换掉 ``add_messages`` 换来的是 checkpoint 体积（实测 34x），代价是这条通道不再有
``add_messages`` 的全部行为。本文件锁住 Lumi 真正依赖的那几条，任何一条变了都要
在这里先变红：

- 同 id 替换（``persist_partial_reply`` 的承重墙：半截回复被全文原地顶掉）
- ``RemoveMessage`` 删除（rewind 截断）
- ``Overwrite`` 整体替换（压缩写回）
- 离线写入自动补 id，含 Overwrite 内的消息，读回保持稳定
- 离线 Overwrite 生成独立快照，同时保留压缩前历史
"""

from __future__ import annotations

from typing import Annotated, TypedDict
from unittest.mock import patch

import pytest
from conftest import apply_messages_update, messages_channel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import Overwrite

from lumi.agents.core.graph import (
    CHECKPOINT_AES_KEY_ENV,
    LumiAgent,
    _checkpoint_serde,
)


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
        Overwrite(value=[HumanMessage("<compact>", id="s1")]),
    )
    assert [m.id for m in merged] == ["s1"]


@pytest.mark.asyncio
async def test_offline_writes_preserve_existing_ids():
    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-delta-node"}}
    await graph.aupdate_state(
        config,
        {"messages": [HumanMessage("h", id="h1")]},
        as_node="OfflineFlush",
    )
    snapshot = (await graph.aget_state(config)).values["messages"]
    assert snapshot[0].id == "h1"


@pytest.mark.asyncio
@pytest.mark.parametrize("overwrite", [False, True])
async def test_offline_writes_assign_stable_ids(overwrite):
    """LangGraph 1.2.15 为普通与 Overwrite 写入补 id；重读和按 id 删除均可用。"""
    graph = LumiAgent(checkpointer=MemorySaver()).graph
    config = {"configurable": {"thread_id": "t-delta-raw"}}
    messages = [HumanMessage("没有 id")]
    await graph.aupdate_state(
        config,
        {"messages": Overwrite(value=messages) if overwrite else messages},
        as_node="OfflineFlush",
    )
    snapshot = (await graph.aget_state(config)).values["messages"]
    message_id = snapshot[0].id
    assert message_id
    assert (await graph.aget_state(config)).values["messages"][0].id == message_id
    await graph.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=message_id)]},
        as_node="OfflineFlush",
    )
    assert (await graph.aget_state(config)).values["messages"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
async def test_offline_compaction_restores_without_ancestor_replay(
    tmp_path, monkeypatch, encrypted
):
    """压缩快照重开数据库后可独立恢复，压缩前的原文仍可按 checkpoint 读取。"""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if encrypted:
        monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    else:
        monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    db = str(tmp_path / "compaction.db")
    config = {"configurable": {"thread_id": "compaction"}}
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        saver.serde = _checkpoint_serde()
        graph = LumiAgent(checkpointer=saver).graph
        before = await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage("旧问题", id="h1"),
                    AIMessage("旧回答", id="a1"),
                ]
            },
            as_node="OfflineFlush",
        )
        await graph.aupdate_state(
            config,
            {"messages": Overwrite(value=[HumanMessage("摘要")])},
            as_node="OfflineFlush",
        )

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        saver.serde = _checkpoint_serde()
        graph = LumiAgent(checkpointer=saver).graph
        with patch.object(
            saver,
            "aget_delta_channel_history",
            side_effect=AssertionError("压缩后的快照不应回放祖先"),
        ):
            messages = (await graph.aget_state(config)).values["messages"]
        assert [m.content for m in messages] == ["摘要"]
        assert messages[0].id
        old = (await graph.aget_state(before)).values["messages"]
        assert [(m.content, m.id) for m in old] == [("旧问题", "h1"), ("旧回答", "a1")]


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

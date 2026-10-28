import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from lumi.agents.core.graph import CHECKPOINT_AES_KEY_ENV, LumiAgent, _checkpoint_serde
from lumi.agents.core.meta_message import (
    HISTORY_ID_KEY,
    HISTORY_KEY,
    INTERJECTION_KEY,
)
from lumi.agents.core.preprocessing.compact import build_compacted_update
from lumi.gateway.session import _history_items
from lumi.sessions.history import HistoryReader
from tests.gateway.toy_graph import bridge_with


@pytest.fixture(params=[False, True], ids=["plain", "encrypted"])
async def history_graph(tmp_path, monkeypatch, request):
    if request.param:
        monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    else:
        monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    async with AsyncSqliteSaver.from_conn_string(
        str(tmp_path / "checkpoints.db")
    ) as saver:
        saver.serde = _checkpoint_serde()
        yield LumiAgent(checkpointer=saver).graph


def config(thread="thread"):
    return {"configurable": {"thread_id": thread}}


async def write(graph, messages, thread="thread"):
    return await graph.aupdate_state(
        config(thread), {"messages": messages}, as_node="OfflineFlush"
    )


def turn(number, text=None):
    return [
        HumanMessage(text or f"question {number}", id=f"h{number}"),
        AIMessage(text or f"answer {number}", id=f"a{number}"),
    ]


async def compress(graph, keep=0, legacy=False):
    before = await graph.aget_state(config())
    messages = before.values["messages"]
    update = build_compacted_update(
        messages,
        messages[-keep:] if keep else [],
        "compact",
        checkpoint_id=before.config["configurable"]["checkpoint_id"],
    )
    if legacy:
        for msg in update["messages"].value:
            msg.additional_kwargs.pop(HISTORY_KEY, None)
            msg.additional_kwargs.pop(HISTORY_ID_KEY, None)
    await graph.aupdate_state(config(), update, as_node="OfflineFlush")
    if legacy:
        await migrate_graph(graph)
    return before


async def read_all(graph, limit=3, reader=None):
    reader = reader or HistoryReader()
    page = await reader.page(graph, "thread", _history_items, limit=limit)
    items = page["items"]
    for _ in range(100):
        if not page["next_cursor"]:
            return items
        page = await reader.page(
            graph, "thread", _history_items, cursor=page["next_cursor"], limit=limit
        )
        assert not page.get("history_unavailable")
        items = page["items"] + items
    pytest.fail("分页游标没有前进")


@pytest.mark.parametrize("legacy", [True])
async def test_multiple_compactions_preserve_every_original_once(history_graph, legacy):
    graph = history_graph
    original = [m for n in range(1, 11) for m in turn(n, "repeated text")]
    await write(graph, original[:8])
    await compress(graph, keep=2, legacy=legacy)
    await write(graph, original[8:16])
    await compress(graph, keep=2, legacy=legacy)
    await write(graph, original[16:])
    reader = HistoryReader()
    latest = await reader.page(graph, "thread", _history_items, limit=4)
    assert [it["id"] for it in latest["items"]] == [
        it["id"] for it in _history_items(original[16:])
    ]
    items = await read_all(graph, reader=reader)
    assert [(it["kind"], it["text"]) for it in items] == [
        (it["kind"], it["text"]) for it in _history_items(original)
    ]
    assert len({it["id"] for it in items}) == 20
    assert all("compact" not in it.get("text", "") for it in items)
    assert not any(it.get("message_id") for it in items[:-6])


@pytest.mark.parametrize("legacy", [True])
async def test_default_page_preserves_internal_retained_anchors(history_graph, legacy):
    graph = history_graph
    original = turn(0) + [HumanMessage("pending", id="pending")]
    for n in range(3):
        original += [
            AIMessage(
                "",
                id=f"ai{n}",
                tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
            ),
            ToolMessage(f"output {n}", id=f"out{n}", tool_call_id=f"tc{n}"),
        ]
    await write(graph, original)
    await compress(graph, keep=4, legacy=legacy)
    reader = HistoryReader()
    current = await reader.page(graph, "thread", _history_items)
    page = await reader.page(
        graph, "thread", _history_items, cursor=current["next_cursor"]
    )
    assert page["order"] == [
        "h0:user:0",
        "a0:assistant",
        current["items"][0]["id"],
        "tool:tc0",
    ]
    assert [it["id"] for it in page["items"]] == [
        "h0:user:0",
        "a0:assistant",
        "tool:tc0",
    ]
    assert page["insert_before"] == "tool:tc1"


@pytest.mark.parametrize("legacy", [True])
async def test_rewind_preserves_earlier_archives_without_reintroducing_future(
    history_graph, legacy
):
    graph = history_graph
    await write(graph, turn(0))
    await compress(graph, legacy=legacy)
    await write(graph, turn(1) + [HumanMessage("target", id="target")])
    for n in range(3):
        await write(
            graph,
            [
                AIMessage(
                    "",
                    id=f"ai{n}",
                    tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
                ),
                ToolMessage(f"output {n}", id=f"out{n}", tool_call_id=f"tc{n}"),
            ],
        )
    await compress(graph, keep=4, legacy=legacy)
    await write(graph, [AIMessage("completed", id="completed")])
    target = next(
        m
        for m in (await graph.aget_state(config())).values["messages"]
        if m.content == "target"
    )
    bridge = bridge_with(config(), graph)
    await bridge.rewind_before_message(target.id)
    await write(
        graph, [HumanMessage("edited", id="edited"), AIMessage("fresh", id="fresh")]
    )
    items = await read_all(graph, limit=50)
    assert [it["text"] for it in items] == [
        "question 0",
        "answer 0",
        "question 1",
        "answer 1",
        "edited",
        "fresh",
    ]


@pytest.mark.parametrize("legacy", [True])
async def test_rewind_of_retained_interjection_keeps_only_preceding_tool_results(
    history_graph, legacy
):
    graph = history_graph
    messages = [HumanMessage("first", id="first")]
    for n in range(3):
        if n == 1:
            messages.append(
                HumanMessage(
                    "correction",
                    id="correction",
                    additional_kwargs={INTERJECTION_KEY: True},
                )
            )
        messages += [
            AIMessage(
                "",
                id=f"ai{n}",
                tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
            ),
            ToolMessage(f"output {n}", id=f"out{n}", tool_call_id=f"tc{n}"),
        ]
    await write(graph, messages)
    await compress(graph, keep=2, legacy=legacy)
    await write(graph, [AIMessage("completed", id="completed")])
    target = next(
        m
        for m in (await graph.aget_state(config())).values["messages"]
        if m.content == "correction"
    )
    await bridge_with(config(), graph).rewind_before_message(target.id)
    await write(
        graph, [HumanMessage("edited", id="edited"), AIMessage("fresh", id="fresh")]
    )
    items = await read_all(graph, limit=50)
    assert [it.get("text", it.get("tool_call_id")) for it in items] == [
        "first",
        "tc0",
        "edited",
        "fresh",
    ]


@pytest.mark.parametrize("legacy", [True])
async def test_editing_completed_ptl_turn_forks_before_its_archived_future(
    history_graph, legacy
):
    graph = history_graph
    original = turn(0) + [HumanMessage("original request", id="target")]
    for n in range(5):
        original += [
            AIMessage(
                "",
                id=f"ai{n}",
                tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
            ),
            ToolMessage(f"output {n}", id=f"out{n}", tool_call_id=f"tc{n}"),
        ]
    await write(graph, original[:9])
    await compress(graph, keep=4, legacy=legacy)
    await write(graph, original[9:])
    await compress(graph, keep=4, legacy=legacy)
    await write(graph, [AIMessage("completed original turn", id="completed")])
    bridge = bridge_with(config(), graph)
    target = next(
        m
        for m in (await graph.aget_state(config())).values["messages"]
        if m.content == "original request"
    )
    await bridge.rewind_before_message(target.id)
    state = await graph.aget_state(config())
    assert [m.content for m in state.values["messages"]] == ["question 0", "answer 0"]
    await write(
        graph,
        [HumanMessage("edited", id="edited"), AIMessage("fresh answer", id="fresh")],
    )
    items = await read_all(graph, limit=50)
    assert [it.get("text") for it in items] == [
        "question 0",
        "answer 0",
        "edited",
        "fresh answer",
    ]
    assert not any(it["kind"] == "tool" for it in items)


@pytest.mark.parametrize("legacy", [True])
async def test_pending_user_kept_before_tail_does_not_reorder_archived_tool_rounds(
    history_graph, legacy
):
    graph = history_graph
    original = turn(0) + [HumanMessage("pending", id="pending")]
    for n in range(3):
        original += [
            AIMessage(
                "",
                id=f"tool-ai-{n}",
                tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
            ),
            ToolMessage(f"output {n}", id=f"tool-result-{n}", tool_call_id=f"tc{n}"),
        ]
    await write(graph, original)
    await compress(graph, keep=4, legacy=legacy)
    reader = HistoryReader()
    page = await reader.page(graph, "thread", _history_items, limit=50)
    items = page["items"]
    while page["next_cursor"]:
        page = await reader.page(
            graph, "thread", _history_items, cursor=page["next_cursor"], limit=1
        )
        anchor = next(
            (i for i, it in enumerate(items) if it["id"] == page.get("insert_before")),
            0,
        )
        existing = {it["id"] for it in items}
        items[anchor:anchor] = [it for it in page["items"] if it["id"] not in existing]
    assert [(it["kind"], it.get("text"), it.get("output")) for it in items] == [
        (it["kind"], it.get("text"), it.get("output"))
        for it in _history_items(original)
    ]


async def test_legacy_full_snapshots_without_delta_writes_are_readable(history_graph):
    graph = history_graph
    builder = StateGraph(MessagesState)
    builder.add_node("OfflineFlush", lambda _: {})
    builder.add_edge(START, "OfflineFlush")
    builder.add_edge("OfflineFlush", END)
    legacy = builder.compile(checkpointer=graph.checkpointer)
    await write(legacy, turn(1) + turn(2))
    await compress(legacy, keep=2, legacy=True)
    await write(legacy, turn(3))
    async with graph.checkpointer.conn.execute(
        "DELETE FROM writes WHERE thread_id = ?", ("thread",)
    ):
        await graph.checkpointer.conn.commit()
    items = await read_all(graph, limit=1)
    assert [it["text"] for it in items] == [
        m.content for n in range(1, 4) for m in turn(n)
    ]


async def migrate_graph(graph):
    from pathlib import Path

    from lumi._migrate.checkpoints import migrate_checkpoints

    cursor = await graph.checkpointer.conn.execute("PRAGMA database_list")
    row = await cursor.fetchone()
    await cursor.close()
    await migrate_checkpoints(Path(row[2]).parent, rename=str, apply=True)

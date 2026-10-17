"""真实 SQLite checkpoint 的分段读取、旧压缩兼容和分页配对。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import graph_run_config, resolved
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from lumi.agents.core import graph as graph_module
from lumi.agents.core import nodes
from lumi.agents.core.graph import CHECKPOINT_AES_KEY_ENV, LumiAgent, _checkpoint_serde
from lumi.agents.core.meta_message import HISTORY_ID_KEY, HISTORY_KEY, INTERJECTION_KEY
from lumi.agents.core.preprocessing import compact
from lumi.agents.core.preprocessing.compact import build_compacted_update
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.gateway.bridge import AgentBridge
from lumi.gateway.session import _history_items, _load_history
from lumi.sessions.history import HistoryReader
from lumi.sessions.thread_runs import thread_runs
from tests.gateway.test_message_retry_e2e import _no_tools
from tests.gateway.toy_graph import bridge_with


@pytest.fixture(params=[False, True], ids=["plain", "encrypted"])
async def history_graph(tmp_path, monkeypatch, request):
    if request.param:
        monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    else:
        monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "history.db")) as saver:
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
        "summary",
        checkpoint_id=before.config["configurable"]["checkpoint_id"],
    )
    if legacy:
        for msg in update["messages"].value:
            msg.additional_kwargs.pop(HISTORY_KEY, None)
            msg.additional_kwargs.pop(HISTORY_ID_KEY, None)
    await graph.aupdate_state(config(), update, as_node="OfflineFlush")
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


@pytest.mark.parametrize("legacy", [False, True])
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
    assert all("summary" not in it.get("text", "") for it in items)
    assert not any(it.get("message_id") for it in items[:-6])


async def test_tools_are_paired_before_page_slicing(history_graph):
    graph = history_graph
    messages = [
        HumanMessage("q", id="h"),
        AIMessage(
            "tool explanation",
            id="ai",
            tool_calls=[{"id": "tc", "name": "bash", "args": {"command": "pwd"}}],
        ),
        ToolMessage("complete result", tool_call_id="tc", id="result"),
        AIMessage("done", id="done"),
    ]
    await write(graph, messages)
    await compress(graph)
    await write(graph, turn(2))
    items = await read_all(graph, limit=1)
    assert len(items) == 6
    tool = next(it for it in items if it["kind"] == "tool")
    assert tool["output"] == "complete result" and tool["tool_call_id"] == "tc"


@pytest.mark.parametrize("legacy", [False, True])
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


@pytest.mark.parametrize("legacy", [False, True])
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


@pytest.mark.parametrize("legacy", [False, True])
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


async def test_rewind_fails_without_mutation_if_required_original_checkpoint_is_missing(
    history_graph,
):
    graph = history_graph
    await write(
        graph,
        [
            HumanMessage("target", id="target"),
            AIMessage(
                "", id="tool-ai", tool_calls=[{"id": "tc", "name": "bash", "args": {}}]
            ),
            ToolMessage("result", id="result", tool_call_id="tc"),
        ],
    )
    before = await compress(graph, keep=2)
    await write(graph, [AIMessage("completed", id="completed")])
    snapshot = await graph.aget_state(config())
    target = next(m for m in snapshot.values["messages"] if m.content == "target")
    async with graph.checkpointer.conn.execute(
        "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
        ("thread", before.config["configurable"]["checkpoint_id"]),
    ):
        await graph.checkpointer.conn.commit()
    with pytest.raises(ValueError, match="无法安全回退"):
        await bridge_with(config(), graph).rewind_before_message(target.id)
    after = await graph.aget_state(config())
    assert after.config == snapshot.config
    assert after.values["messages"] == snapshot.values["messages"]


@pytest.mark.parametrize("legacy", [False, True])
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


@pytest.mark.parametrize("legacy", [False, True])
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


async def test_missing_archive_is_reported_without_losing_latest_messages(
    history_graph,
):
    graph = history_graph
    await write(graph, turn(1))
    before = await compress(graph)
    await write(graph, turn(2))
    async with graph.checkpointer.conn.execute(
        "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
        ("thread", before.config["configurable"]["checkpoint_id"]),
    ):
        await graph.checkpointer.conn.commit()
    reader = HistoryReader()
    page = await reader.page(graph, "thread", _history_items)
    assert len(page["items"]) == 2
    missing = await reader.page(
        graph, "thread", _history_items, cursor=page["next_cursor"]
    )
    assert missing["history_unavailable"] and missing["next_cursor"] is None


async def test_paging_uses_fixed_checkpoint_when_new_messages_and_compaction_arrive(
    history_graph,
):
    graph = history_graph
    original = [m for n in range(5) for m in turn(n)]
    await write(graph, original)
    reader = HistoryReader()
    first = await reader.page(graph, "thread", _history_items, limit=3)
    await write(graph, turn(5))
    await compress(graph)
    second = await reader.page(
        graph, "thread", _history_items, cursor=first["next_cursor"], limit=3
    )
    assert [it["id"] for it in second["items"]] == [
        it["id"] for it in _history_items(original)[4:7]
    ]


async def test_rewound_retained_message_does_not_reappear_in_archive(history_graph):
    graph = history_graph
    await write(graph, turn(1) + turn(2))
    await compress(graph, keep=2)
    state = await graph.aget_state(config())
    await write(graph, [RemoveMessage(id=m.id) for m in state.values["messages"][1:]])
    await write(graph, turn(3))
    items = await read_all(graph)
    assert [it["text"] for it in items] == [
        "question 1",
        "answer 1",
        "question 3",
        "answer 3",
    ]


async def test_segment_is_restored_once_for_multiple_pages(history_graph):
    graph = history_graph
    await write(graph, [m for n in range(10) for m in turn(n)])
    reader = HistoryReader()
    with patch.object(graph, "aget_state", wraps=graph.aget_state) as restore:
        items = await read_all(graph, limit=2, reader=reader)
    assert len(items) == 20 and restore.await_count == 1


async def test_deleting_a_thread_invalidates_cached_pages(history_graph):
    graph = history_graph
    await write(graph, turn(1))
    reader = HistoryReader()
    page = await reader.page(graph, "thread", _history_items, limit=1)
    async with thread_runs.lock_for("thread").cancel_and_hold():
        await graph.checkpointer.adelete_thread("thread")
    missing = await reader.page(
        graph, "thread", _history_items, cursor=page["next_cursor"]
    )
    assert missing["history_unavailable"] and missing["items"] == []


async def test_cursor_is_bound_to_thread_and_invalid_cursors_are_rejected(
    history_graph,
):
    graph = history_graph
    await write(graph, turn(1))
    reader = HistoryReader()
    first = await reader.page(graph, "thread", _history_items, limit=1)
    for cursor in [first["next_cursor"], "garbage", "W10="]:
        with pytest.raises(ValueError, match="游标"):
            await reader.page(graph, "other", _history_items, cursor=cursor)


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


async def test_rpc_older_pages_do_not_replace_live_usage_or_pending(history_graph):
    graph = history_graph
    await write(graph, turn(1) + turn(2))
    bridge = SimpleNamespace(
        graph=graph,
        history_reader=HistoryReader(),
        current_thread_id="thread",
        pending_interjections=lambda: [],
    )
    first = await _load_history(bridge, {"thread_id": "thread", "limit": 1})
    assert "pending" in first and first["user_message_ids"]
    older = await _load_history(
        bridge, {"thread_id": "thread", "limit": 1, "cursor": first["next_cursor"]}
    )
    assert "pending" not in older and "usage" not in older


async def test_online_compaction_links_to_the_actual_input_checkpoint(history_graph):
    graph = history_graph
    await write(graph, turn(1))
    builder = StateGraph(LumiAgentState)
    builder.add_node("Summarizer", nodes.summarizer)
    builder.add_edge(START, "Summarizer")
    builder.add_edge("Summarizer", END)
    online = builder.compile(checkpointer=graph.checkpointer)
    fake_config = graph_run_config(context_length=2)
    with (
        patch.object(nodes, "get_config", return_value=fake_config),
        patch.object(nodes, "resolve", return_value=resolved(context_window=2)),
        patch.object(compact, "get_config", return_value=fake_config),
        patch.object(
            compact, "run_summary", new=AsyncMock(return_value=("summary", 0))
        ),
    ):
        await online.ainvoke(
            {"messages": [HumanMessage("pending", id="pending")]},
            config(),
            context=LumiAgentContext(model_name="fake"),
        )
    messages = (await online.aget_state(config())).values["messages"]
    link = messages[0].additional_kwargs[HISTORY_KEY]
    before = await graph.aget_state(
        {
            "configurable": {
                "thread_id": "thread",
                "checkpoint_id": link["checkpoint_id"],
            }
        }
    )
    assert [m.content for m in before.values["messages"]] == [
        "question 1",
        "answer 1",
        "pending",
    ]
    items = await read_all(online, limit=1)
    assert [it["text"] for it in items] == ["question 1", "answer 1", "pending"]


@pytest.mark.parametrize("action", ["regenerate", "edit_resend"])
async def test_streamed_time_travel_after_ptl_uses_the_restored_branch(
    tmp_path, isolated_config, action
):
    seen = []

    async def model(state, runtime):
        seen.extend(state["messages"])
        return {"messages": [AIMessage("new response", id="new-response")]}

    bridge = AgentBridge()
    try:
        with (
            patch.object(graph_module, "call_model", new=model),
            patch.object(AgentBridge, "_build_tools", new=_no_tools),
        ):
            await bridge.initialize(project_dir=str(tmp_path))
            messages = turn(0) + [HumanMessage("original request", id="target")]
            for n in range(3):
                messages += [
                    AIMessage(
                        "",
                        id=f"ai{n}",
                        tool_calls=[{"id": f"tc{n}", "name": "bash", "args": {}}],
                    ),
                    ToolMessage(f"old result {n}", id=f"out{n}", tool_call_id=f"tc{n}"),
                ]
            await bridge.flush_offline({"messages": messages})
            before = await bridge.graph.aget_state(bridge._config)
            await bridge.flush_offline(
                build_compacted_update(
                    messages,
                    messages[-4:],
                    "obsolete future summary",
                    checkpoint_id=before.config["configurable"]["checkpoint_id"],
                )
            )
            await bridge.flush_offline(
                {"messages": [AIMessage("completed", id="completed")]}
            )
            target = next(
                m
                for m in await bridge.snapshot_messages()
                if m.content == "original request"
            )
            stream = (
                bridge.stream_regenerate(target.id)
                if action == "regenerate"
                else bridge.stream_edit_resend(target.id, "edited")
            )
            events = [event async for event in stream]
            assert not any(event.kind.value == "error" for event in events)
            assert seen and not any(m.type == "tool" for m in seen)
            assert not any("obsolete future summary" in str(m.content) for m in seen)
            items = _history_items(await bridge.snapshot_messages())
            expected = "original request" if action == "regenerate" else "edited"
            assert [it["text"] for it in items] == [
                "question 0",
                "answer 0",
                expected,
                "new response",
            ]
    finally:
        await bridge.close()

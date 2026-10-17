"""独立 SQLite 分支：多次 fork、原会话重答/删除、压缩原文及附件生命周期。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Overwrite

from lumi.agents.core import graph as graph_module
from lumi.agents.core.graph import CHECKPOINT_AES_KEY_ENV, LumiAgent, _checkpoint_serde
from lumi.agents.core.meta_message import (
    HISTORY_ID_KEY,
    HISTORY_KEY,
    declared_file_paths,
    history_message_ids,
)
from lumi.agents.core.preprocessing.compact import build_compacted_update
from lumi.agents.core.state import LumiAgentContext
from lumi.gateway import uploads
from lumi.gateway.bridge import AgentBridge
from lumi.gateway.broadcast import BroadcastHub
from lumi.gateway.protocol import bridge_event_to_wire
from lumi.gateway.session import (
    GatewaySession,
    _delete_session,
    _fork_session,
    _history_items,
)
from lumi.sessions import fork, session_meta
from lumi.sessions.history import HistoryReader
from lumi.sessions.session_store import list_sessions
from tests.gateway.test_gateway_session import FakeChannel
from tests.gateway.toy_graph import bridge_with


@pytest.fixture(params=[False, True], ids=["plain", "encrypted"])
async def database(tmp_path, monkeypatch, isolated_config, request):
    if request.param:
        monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    else:
        monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    monkeypatch.setattr(fork, "_fork_lock", asyncio.Lock())

    async def model(state, runtime):
        return {"messages": [AIMessage("new answer", id=uuid4().hex)]}

    monkeypatch.setattr(graph_module, "call_model", model)
    path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        saver.serde = _checkpoint_serde()
        yield LumiAgent(checkpointer=saver), path


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(uploads, "uploads_dir", lambda: root)
    return root


def config(thread="source"):
    return {
        "configurable": {"thread_id": thread},
        "metadata": {"workspace_dir": "/project"},
    }


def turn(number):
    return [
        HumanMessage(f"question {number}", id=f"h{number}"),
        AIMessage(f"answer {number}", id=f"a{number}"),
    ]


async def seed(graph, count=10):
    return await graph.aupdate_state(
        config(),
        {"messages": [m for n in range(1, count + 1) for m in turn(n)]},
        as_node="OfflineFlush",
    )


async def branch(graph, point, request_id=None, thread="source"):
    session = SimpleNamespace(_bridge=SimpleNamespace(graph=graph))
    return await _fork_session(
        session,
        {"thread_id": thread, **point, "request_id": request_id or str(uuid4())},
    )


async def snapshot(graph, thread="source"):
    return await graph.aget_state(config(thread))


async def point_for(graph, number, thread="source"):
    state = await snapshot(graph, thread)
    return {
        "checkpoint_id": state.config["configurable"]["checkpoint_id"],
        "message_id": f"a{number}",
    }


async def all_items(graph, thread):
    reader = HistoryReader()
    page = await reader.page(graph, thread, _history_items, limit=2)
    items = page["items"]
    while page["next_cursor"]:
        page = await reader.page(
            graph, thread, _history_items, cursor=page["next_cursor"], limit=2
        )
        assert not page.get("history_unavailable")
        items = page["items"] + items
    return items


async def test_repeated_nested_forks_and_retry_have_persistent_distinct_titles(
    database,
):
    agent, _ = database
    graph = agent.graph
    await seed(graph)
    session_meta.update_meta("source", title="A")
    point = await point_for(graph, 9)
    before = await snapshot(graph)
    request_id = str(uuid4())
    first = await branch(graph, point, request_id)
    assert await branch(graph, point, request_id) == first
    second = await branch(graph, point)
    assert [first["title"], second["title"]] == ["A · 分支 1", "A · 分支 2"]
    assert first["thread_id"] != second["thread_id"]
    assert (await snapshot(graph)).config == before.config
    assert len((await snapshot(graph, first["thread_id"])).values["messages"]) == 18
    await graph.checkpointer.adelete_thread(second["thread_id"])
    session_meta.delete_meta(second["thread_id"])
    # 重启后的元数据重读、删除后的编号和从分支再次 fork 使用同一序列。
    nested_point = await point_for(graph, 5, first["thread_id"])
    third = await branch(graph, nested_point, thread=first["thread_id"])
    assert third["title"] == "A · 分支 3"
    fourth, fifth = await asyncio.gather(branch(graph, point), branch(graph, point))
    assert {fourth["title"], fifth["title"]} == {"A · 分支 4", "A · 分支 5"}
    listed = await list_sessions(graph)
    assert first["thread_id"] in {s.thread_id for s in listed}
    assert first["workspace_dir"] == "/project"


async def test_fork_at_ninth_turn_survives_source_regenerate_at_fifth(
    database, tmp_path
):
    agent, _ = database
    graph = agent.graph
    await seed(graph)
    result = await branch(graph, await point_for(graph, 9))
    tid = result["thread_id"]
    branch_before = await snapshot(graph, tid)
    bridge = bridge_with(config(), graph)
    bridge._context = LumiAgentContext(model_name="fake", project_dir=tmp_path)
    events = [event async for event in bridge.stream_regenerate("h5")]
    assert not any(event.kind.value == "error" for event in events)
    source = await snapshot(graph)
    complete = next(event for event in events if event.kind.value == "turn.complete")
    point = bridge_event_to_wire(complete, "source")["params"]["payload"]["fork_point"]
    assert point["checkpoint_id"] == source.config["configurable"]["checkpoint_id"]
    assert point["message_id"] == source.values["messages"][-1].id
    assert [it["text"] for it in _history_items(source.values["messages"])] == [
        m.content for n in range(1, 5) for m in turn(n)
    ] + ["question 5", "new answer"]
    branch_after = await snapshot(graph, tid)
    assert branch_after.config == branch_before.config
    assert [m.content for m in branch_after.values["messages"]] == [
        m.content for n in range(1, 10) for m in turn(n)
    ]
    assert branch_after.next == () and branch_after.tasks == ()
    await graph.ainvoke(
        {"messages": [HumanMessage("new direction")]},
        config(tid),
        context=LumiAgentContext(model_name="fake"),
    )
    assert (await snapshot(graph, tid)).values["messages"][-1].content == "new answer"
    assert (await snapshot(graph)).config == source.config


@pytest.mark.parametrize("legacy", [False, True])
async def test_compressed_fork_survives_source_delete_and_database_reopen(
    database, legacy
):
    agent, path = database
    graph = agent.graph
    await seed(graph, 4)
    for start, stop in [(5, 8), (9, 10)]:
        state = await snapshot(graph)
        messages = state.values["messages"]
        update = build_compacted_update(
            messages,
            messages[-2:],
            "summary",
            checkpoint_id=state.config["configurable"]["checkpoint_id"],
        )
        if legacy:
            for message in update["messages"].value:
                message.additional_kwargs.pop(HISTORY_KEY, None)
                message.additional_kwargs.pop(HISTORY_ID_KEY, None)
        await graph.aupdate_state(config(), update, as_node="OfflineFlush")
        await graph.aupdate_state(
            config(),
            {"messages": [m for n in range(start, stop + 1) for m in turn(n)]},
            as_node="OfflineFlush",
        )
    result = await branch(graph, await point_for(graph, 9))
    tid = result["thread_id"]
    before = await all_items(graph, tid)
    assert [item["text"] for item in before] == [
        m.content for n in range(1, 10) for m in turn(n)
    ]
    source_bridge = bridge_with(config(), graph)
    source_bridge._agent = agent
    await _delete_session(
        SimpleNamespace(_bridge=source_bridge), {"thread_id": "source"}
    )
    assert not (await snapshot(graph)).values
    # 新连接从磁盘恢复，不复用 HistoryReader 缓存或原会话的增量写入。
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        saver.serde = _checkpoint_serde()
        restored = LumiAgent(checkpointer=saver).graph
        after = await all_items(restored, tid)
        assert [(it["kind"], it["text"]) for it in after] == [
            (it["kind"], it["text"]) for it in before
        ]
        await restored.ainvoke(
            {"messages": [HumanMessage("still usable")]},
            config(tid),
            context=LumiAgentContext(model_name="fake"),
        )
        assert (await snapshot(restored, tid)).values["messages"][
            -1
        ].content == "new answer"
        nested = await branch(restored, await point_for(restored, 9, tid), thread=tid)
        assert nested["title"].endswith(" · 分支 2")


async def test_archived_reply_and_retained_reply_never_inherit_future_summary(database):
    agent, _ = database
    graph = agent.graph
    await seed(graph)
    before = await snapshot(graph)
    update = build_compacted_update(
        before.values["messages"],
        before.values["messages"][-4:],
        "future contains turn 10",
        checkpoint_id=before.config["configurable"]["checkpoint_id"],
    )
    await graph.aupdate_state(config(), update, as_node="OfflineFlush")
    reader = HistoryReader()
    page = await reader.page(graph, "source", _history_items, limit=200)
    ninth = next(
        it["fork_point"] for it in page["items"] if it.get("text") == "answer 9"
    )
    ninth_result = await branch(graph, ninth)
    ninth_messages = (await snapshot(graph, ninth_result["thread_id"])).values[
        "messages"
    ]
    assert [m.content for m in ninth_messages] == [
        m.content for n in range(1, 10) for m in turn(n)
    ]
    archived = await reader.page(
        graph, "source", _history_items, cursor=page["next_cursor"], limit=200
    )
    fifth = next(
        it["fork_point"] for it in archived["items"] if it.get("text") == "answer 5"
    )
    fifth_result = await branch(graph, fifth)
    assert [
        m.content
        for m in (await snapshot(graph, fifth_result["thread_id"])).values["messages"]
    ] == [m.content for n in range(1, 6) for m in turn(n)]


async def test_fork_shares_original_upload_paths_through_rewind_and_deletion(
    database, upload_root, tmp_path
) -> None:
    agent, _ = database
    graph = agent.graph
    root = upload_root
    attachment = root / "photo.png"
    attachment.write_bytes(b"image bytes")
    backup = root / "photo.png.bak"
    backup.write_bytes(b"backup")
    original_file = tmp_path / "user.txt"
    original_file.write_text("user data")
    human = AgentBridge._build_user_message(
        "look", None, [str(attachment), str(original_file)]
    )
    messages = turn(0) + [
        human,
        AIMessage(
            "",
            id="call",
            tool_calls=[{"id": "tc", "name": "read", "args": {"path": str(backup)}}],
        ),
        ToolMessage(f"read {backup} using {attachment}", tool_call_id="tc", id="tool"),
        AIMessage("done", id="a1"),
    ]
    await graph.aupdate_state(config(), {"messages": messages}, as_node="OfflineFlush")
    first = await branch(graph, await point_for(graph, 1))
    second = await branch(graph, await point_for(graph, 1))
    for result in [first, second]:
        inherited = (await snapshot(graph, result["thread_id"])).values["messages"]
        assert declared_file_paths(inherited[2]) == [
            str(attachment),
            str(original_file),
        ]
        assert inherited[2].content == human.content
        assert inherited[3].tool_calls[0]["args"]["path"] == str(backup)
        assert inherited[4].content == messages[4].content
        assert inherited[4].tool_call_id == inherited[3].tool_calls[0]["id"]
    assert set(root.iterdir()) == {attachment, backup}  # fork 没有创建目录或复制文件
    source_bridge = bridge_with(config(), graph)
    source_bridge._agent = agent
    await source_bridge.rewind_before_message("h0")
    await graph.aupdate_state(config(), {"messages": turn(10)}, as_node="OfflineFlush")
    await _delete_session(
        SimpleNamespace(_bridge=source_bridge), {"thread_id": "source"}
    )
    assert attachment.read_bytes() == b"image bytes"
    await _delete_session(
        SimpleNamespace(_bridge=source_bridge), {"thread_id": first["thread_id"]}
    )
    assert attachment.read_bytes() == b"image bytes"
    await _delete_session(
        SimpleNamespace(_bridge=source_bridge), {"thread_id": second["thread_id"]}
    )
    assert not attachment.exists()  # 最后一个共享引用释放后才回收
    assert backup.read_bytes() == b"backup"
    assert original_file.read_text() == "user data"


@pytest.mark.parametrize("invalid", ["human", "tool-process", "partial", "missing"])
async def test_invalid_fork_points_do_not_create_sessions(database, invalid):
    agent, _ = database
    graph = agent.graph
    messages = turn(1) + [
        AIMessage(
            "working",
            id="process",
            tool_calls=[{"id": "pending", "name": "read", "args": {}}],
        ),
        AIMessage(
            "partial", id="partial", additional_kwargs={"lumi": {"interrupted": True}}
        ),
    ]
    saved = await graph.aupdate_state(
        config(), {"messages": messages}, as_node="OfflineFlush"
    )
    identities = {
        "human": "h1",
        "tool-process": "process",
        "partial": "partial",
        "missing": "absent",
    }
    request_id = str(uuid4())
    with pytest.raises(ValueError):
        await branch(
            graph,
            {
                "checkpoint_id": saved["configurable"]["checkpoint_id"],
                "message_id": identities[invalid],
            },
            request_id,
        )
    assert not (await snapshot(graph, f"t-{request_id.replace('-', '')}")).values


async def test_failed_fork_rolls_back_checkpoints_without_removing_original_attachments(
    database, upload_root, monkeypatch
) -> None:
    agent, _ = database
    graph = agent.graph
    root = upload_root
    attachment = root / "image.png"
    attachment.write_bytes(b"image")
    human = AgentBridge._build_user_message("look", None, [str(attachment)])
    await graph.aupdate_state(
        config(),
        {"messages": [human, AIMessage("done", id="a1")]},
        as_node="OfflineFlush",
    )
    point = await point_for(graph, 1)
    request_id = str(uuid4())

    update_meta = fork.update_meta

    def fail(thread_id, **fields):
        if fields.get("title"):
            raise OSError("disk full")
        return update_meta(thread_id, **fields)

    monkeypatch.setattr(fork, "update_meta", fail)
    with pytest.raises(OSError, match="disk full"):
        await branch(graph, point, request_id)
    assert not (await snapshot(graph, f"t-{request_id.replace('-', '')}")).values
    assert list(root.iterdir()) == [attachment]
    assert history_message_ids((await snapshot(graph)).values["messages"])[-1] == "a1"


async def test_rpc_creates_fork_without_switching_or_running_source_connection(
    database,
):
    agent, _ = database
    graph = agent.graph
    await seed(graph, 2)
    bridge = bridge_with(config(), graph)
    channel = FakeChannel()
    session = GatewaySession(bridge, channel, BroadcastHub())
    before = await snapshot(graph)
    await session.handle_frame(
        {
            "id": 1,
            "method": "fork_session",
            "params": {
                "thread_id": "source",
                **await point_for(graph, 1),
                "request_id": str(uuid4()),
            },
        }
    )
    await asyncio.gather(*session._rpc_tasks)
    result = channel.responses()[0]["result"]
    assert result["thread_id"] != "source"
    assert bridge.current_thread_id == "source"
    assert (await snapshot(graph)).config == before.config
    assert channel.events() == []


async def test_model_configuration_is_inherited_without_copying_goal_or_pins(
    database, monkeypatch
):
    from lumi.sessions.session_model import SessionModel

    agent, _ = database
    graph = agent.graph
    await seed(graph, 1)
    session_meta.update_meta("source", title="A", goal="source goal", pinned=True)
    monkeypatch.setattr(
        fork.session_model,
        "resolve",
        lambda _: SessionModel("chosen", "profile", "high", True),
    )
    result = await branch(graph, await point_for(graph, 1))
    meta = session_meta.load_all()[result["thread_id"]]
    assert (meta["model"], meta["model_provider"], meta["effort"]) == (
        "chosen",
        "profile",
        "high",
    )
    assert "goal" not in meta and "pinned" not in meta


async def test_cancelling_fork_keeps_original_uploads_and_removes_pending_thread(
    database, upload_root, monkeypatch
) -> None:
    agent, _ = database
    graph = agent.graph
    root = upload_root
    attachment = root / "photo.png"
    attachment.write_bytes(b"image")
    await graph.aupdate_state(
        config(),
        {
            "messages": [
                AgentBridge._build_user_message("look", None, [str(attachment)]),
                AIMessage("done", id="a1"),
            ]
        },
        as_node="OfflineFlush",
    )
    entered, release = asyncio.Event(), asyncio.Event()
    request_id = str(uuid4())
    target = "t-" + request_id.replace("-", "")
    update = graph.aupdate_state

    async def paused(config, *args, **kwargs):
        saved = await update(config, *args, **kwargs)
        if config["configurable"]["thread_id"] == target:
            entered.set()
            await release.wait()
        return saved

    monkeypatch.setattr(graph, "aupdate_state", paused)
    task = asyncio.create_task(branch(graph, await point_for(graph, 1), request_id))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert target not in {session.thread_id for session in await list_sessions(graph)}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(root.iterdir()) == [attachment]
    assert not (await snapshot(graph, target)).values
    assert target not in session_meta.load_all()


async def test_deleting_compressed_branch_reclaims_its_archived_uploads(
    database, upload_root
) -> None:
    agent, _ = database
    graph = agent.graph
    root = upload_root
    attachment = root / "photo.png"
    attachment.write_bytes(b"image")
    await graph.aupdate_state(
        config(),
        {
            "messages": [
                AgentBridge._build_user_message("look", None, [str(attachment)]),
                AIMessage("done", id="a1"),
            ]
        },
        as_node="OfflineFlush",
    )
    before = await snapshot(graph)
    await graph.aupdate_state(
        config(),
        build_compacted_update(
            before.values["messages"],
            [],
            "summary",
            checkpoint_id=before.config["configurable"]["checkpoint_id"],
        ),
        as_node="OfflineFlush",
    )
    await graph.aupdate_state(config(), {"messages": turn(2)}, as_node="OfflineFlush")
    point = await point_for(graph, 2)
    request_id = str(uuid4())
    result = await branch(graph, point, request_id)
    tid = result["thread_id"]
    shared = session_meta.load_all()[tid]["shared_uploads"]
    assert shared == [str(attachment)]
    bridge = bridge_with(config(), graph)
    bridge._agent = agent
    await _delete_session(SimpleNamespace(_bridge=bridge), {"thread_id": "source"})
    assert await branch(graph, point, request_id) == result
    assert attachment.read_bytes() == b"image"
    await _delete_session(SimpleNamespace(_bridge=bridge), {"thread_id": tid})
    assert not attachment.exists()


async def test_fork_uses_selected_checkpoint_even_after_source_rewind_removes_legacy_summary(
    database,
):
    agent, _ = database
    graph = agent.graph
    before_compaction = await seed(graph, 4)
    before = await snapshot(graph)
    update = build_compacted_update(before.values["messages"], [], "old summary")
    await graph.aupdate_state(config(), update, as_node="OfflineFlush")
    await graph.aupdate_state(
        config(),
        {"messages": [m for n in range(5, 11) for m in turn(n)]},
        as_node="OfflineFlush",
    )
    point = await point_for(graph, 9)
    # 原会话从更早的 checkpoint 重答，旧摘要不再位于当前分支的祖先链上。
    await graph.aupdate_state(
        before_compaction,
        {"messages": Overwrite(turn(1) + turn(2))},
        as_node="OfflineFlush",
    )
    result = await branch(graph, point)
    assert [it["text"] for it in await all_items(graph, result["thread_id"])] == [
        m.content for n in range(1, 10) for m in turn(n)
    ]
    assert len((await snapshot(graph)).values["messages"]) == 4


async def test_pending_fork_is_hidden_and_deletion_waits_for_initialization(
    database, monkeypatch
):
    agent, _ = database
    graph = agent.graph
    await seed(graph, 1)
    before = await snapshot(graph)
    await graph.aupdate_state(
        config(),
        build_compacted_update(
            before.values["messages"],
            [],
            "summary",
            checkpoint_id=before.config["configurable"]["checkpoint_id"],
        ),
        as_node="OfflineFlush",
    )
    await graph.aupdate_state(config(), {"messages": turn(2)}, as_node="OfflineFlush")
    request_id = str(uuid4())
    target = "t-" + request_id.replace("-", "")
    entered, release, deleting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    update = graph.aupdate_state
    writes = 0

    async def paused(config, *args, **kwargs):
        nonlocal writes
        saved = await update(config, *args, **kwargs)
        if config["configurable"]["thread_id"] == target:
            writes += 1
            if writes == 1:
                entered.set()
                await release.wait()
        return saved

    monkeypatch.setattr(graph, "aupdate_state", paused)
    creating = asyncio.create_task(branch(graph, await point_for(graph, 2), request_id))
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert (await snapshot(graph, target)).values
    assert target not in {item.thread_id for item in await list_sessions(graph)}
    bridge = bridge_with(config(), graph)
    bridge._agent = agent

    async def delete_target():
        deleting.set()
        return await _delete_session(
            SimpleNamespace(_bridge=bridge), {"thread_id": target}
        )

    removing = asyncio.create_task(delete_target())
    await deleting.wait()
    assert not removing.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(creating, removing), timeout=5)
    assert not (await snapshot(graph, target)).values
    assert target not in session_meta.load_all()
    assert target not in {item.thread_id for item in await list_sessions(graph)}
    assert (await snapshot(graph)).values


async def test_concurrent_deletion_reclaims_shared_upload_only_after_all_owners_release(
    database, upload_root
) -> None:
    agent, _ = database
    graph = agent.graph
    root = upload_root
    attachment = root / "shared.png"
    attachment.write_bytes(b"shared image")
    await graph.aupdate_state(
        config(),
        {
            "messages": [
                AgentBridge._build_user_message("look", None, [str(attachment)]),
                AIMessage("done", id="a1"),
            ]
        },
        as_node="OfflineFlush",
    )
    first = await branch(graph, await point_for(graph, 1))
    nested = await branch(
        graph, await point_for(graph, 1, first["thread_id"]), thread=first["thread_id"]
    )
    bridge = bridge_with(config(), graph)
    bridge._agent = agent
    # 并发删除其中两个会话，第三个分支仍持有同一原地址。
    await asyncio.gather(
        *[
            _delete_session(SimpleNamespace(_bridge=bridge), {"thread_id": tid})
            for tid in ["source", first["thread_id"]]
        ]
    )
    assert attachment.read_bytes() == b"shared image"
    assert declared_file_paths(
        (await snapshot(graph, nested["thread_id"])).values["messages"][0]
    ) == [str(attachment)]
    # 再建立共享者，并发删除最后两个引用，不能让双方都把清理留给对方。
    last = await branch(
        graph,
        await point_for(graph, 1, nested["thread_id"]),
        thread=nested["thread_id"],
    )
    await asyncio.gather(
        *[
            _delete_session(SimpleNamespace(_bridge=bridge), {"thread_id": tid})
            for tid in [nested["thread_id"], last["thread_id"]]
        ]
    )
    assert not attachment.exists()


async def test_shared_upload_reference_survives_database_reopen(
    database, upload_root
) -> None:
    agent, path = database
    graph = agent.graph
    root = upload_root
    attachment = root / "shared.png"
    attachment.write_bytes(b"shared image")
    await graph.aupdate_state(
        config(),
        {
            "messages": [
                AgentBridge._build_user_message("look", None, [str(attachment)]),
                AIMessage("done", id="a1"),
            ]
        },
        as_node="OfflineFlush",
    )
    result = await branch(graph, await point_for(graph, 1))
    source = bridge_with(config(), graph)
    source._agent = agent
    await _delete_session(SimpleNamespace(_bridge=source), {"thread_id": "source"})
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        saver.serde = _checkpoint_serde()
        restored = LumiAgent(checkpointer=saver)
        inherited = (await snapshot(restored.graph, result["thread_id"])).values[
            "messages"
        ]
        assert declared_file_paths(inherited[0]) == [str(attachment)]
        assert attachment.read_bytes() == b"shared image"
        branch_bridge = bridge_with(config(result["thread_id"]), restored.graph)
        branch_bridge._agent = restored
        await _delete_session(
            SimpleNamespace(_bridge=branch_bridge), {"thread_id": result["thread_id"]}
        )
    assert not attachment.exists()


async def test_branch_regenerate_after_source_delete_stays_visible_and_restores_its_own_history(
    database, tmp_path
):
    agent, _ = database
    graph = agent.graph
    await seed(graph, 4)
    before = await snapshot(graph)
    await graph.aupdate_state(
        config(),
        build_compacted_update(
            before.values["messages"],
            before.values["messages"][-2:],
            "summary",
            checkpoint_id=before.config["configurable"]["checkpoint_id"],
        ),
        as_node="OfflineFlush",
    )
    await graph.aupdate_state(
        config(),
        {"messages": [m for n in range(5, 11) for m in turn(n)]},
        as_node="OfflineFlush",
    )
    result = await branch(graph, await point_for(graph, 9))
    tid = result["thread_id"]
    source = bridge_with(config(), graph)
    source._agent = agent
    await _delete_session(SimpleNamespace(_bridge=source), {"thread_id": "source"})
    inherited = (await snapshot(graph, tid)).values["messages"]
    fourth = next(message for message in inherited if message.content == "question 4")
    branch_bridge = bridge_with(config(tid), graph)
    branch_bridge._context = LumiAgentContext(model_name="fake", project_dir=tmp_path)
    events = [event async for event in branch_bridge.stream_regenerate(fourth.id)]
    assert not any(event.kind.value == "error" for event in events)
    assert [it["text"] for it in await all_items(graph, tid)] == [
        m.content for n in range(1, 4) for m in turn(n)
    ] + ["question 4", "new answer"]
    assert tid in {item.thread_id for item in await list_sessions(graph)}

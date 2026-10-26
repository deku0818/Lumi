"""Regression coverage for per-turn stops, background descendants and durable returns."""

import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import ValidationError

from lumi.agents.core.graph import LumiAgent, create_checkpointer
from lumi.agents.runtime import bg_process
from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    current_thread_id,
    get_task_registry,
    run_background_task,
)
from lumi.agents.runtime.subagents.lumi import translate_lumi_event
from lumi.agents.runtime.subagents.service import (
    SubagentService,
    TimelineWriter,
    result_notification,
)
from lumi.gateway.bridge.core import AgentBridge
from lumi.sessions.subagents import SubagentStore
from lumi.utils.config.models import AgentsConfig


@pytest_asyncio.fixture
async def background_manager(monkeypatch):
    manager = bg_process.BackgroundTaskManager()
    monkeypatch.setattr(bg_process, "_bg_manager", manager)
    yield manager
    await manager.cleanup_all()


async def launch_background(kind, directory, manager, *, wait=False):
    if kind == "bash":
        return await manager.start_task(
            "sleep 30" if wait else "printf finished", None, str(directory)
        )
    entry = BackgroundTaskEntry(
        task_id="workflow-regression",
        kind=TaskKind.WORKFLOW,
        status=TaskStatus.RUNNING,
        label="workflow",
        started_at=0,
        output_file=directory / "workflow.txt",
    )
    get_task_registry().register(entry)

    async def produce():
        if wait:
            await asyncio.Event().wait()
        return "finished"

    entry.async_task = asyncio.create_task(
        run_background_task(
            entry.task_id, entry.output_file, produce, cancel_text="cancelled"
        )
    )
    await asyncio.sleep(
        0
    )  # Enter the workflow's cancellation handler before returning.
    return entry


@pytest.mark.parametrize("kind", ["bash", "workflow"])
async def test_background_tool_completion_continues_owner_and_returns_to_root(
    tmp_path, background_manager, kind
):
    received = asyncio.Event()
    jobs, prompts = [], []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            current_thread_id.set(agent.agent_id)
            prompts.append(turn.prompt)
            if turn.prompt == "start":
                jobs.append(await launch_background(kind, tmp_path, background_manager))
                return "started"
            assert (
                jobs[0].task_id in turn.prompt and "Read the output file" in turn.prompt
            )
            received.set()
            return jobs[0].output_file.read_text()

        async def close(self):
            pass

    service = SubagentService(tmp_path / "sessions.db", lambda _: Worker())
    try:
        first = service.create(
            "root",
            str(tmp_path),
            {"name": "worker"},
            1,
            "start",
            notify=False,
            title="测试任务",
        )
        await service.wait(first["turn_id"])
        await asyncio.wait_for(received.wait(), 3)
        followup = service.store.request("background:" + jobs[0].task_id)
        assert (await service.wait(followup.turn_id))["output"] == "finished"
        assert len(prompts) == 2
        delivery = service.store.pending_deliveries("root")
        assert len(delivery) == 1 and delivery[0]["output"] == "finished"
        queue = get_task_registry().notification_queue
        assert not queue.has_for(first["agent_id"]) and not queue.has_for("root")
        # Repeated terminal notifications must not invoke the model twice.
        get_task_registry().enqueue_notification(jobs[0].task_id)
        assert service.list_agents("root")[0]["turn_count"] == 2
    finally:
        await service.close()


@pytest.mark.parametrize("kind", ["bash", "workflow"])
async def test_deleting_parent_stops_nested_tool_tasks_and_drains_notifications(
    tmp_path, background_manager, kind
):
    jobs, prompts = [], []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            current_thread_id.set(agent.agent_id)
            prompts.append(turn.prompt)
            if turn.prompt == "spawn":
                jobs.append(
                    await launch_background(
                        kind, tmp_path, background_manager, wait=True
                    )
                )
            return "ready"

        async def close(self):
            pass

    service = SubagentService(tmp_path / "sessions.db", lambda _: Worker())
    try:
        owner = service.create(
            "root",
            str(tmp_path),
            {"name": "owner"},
            1,
            "parent",
            notify=False,
            title="测试任务",
        )
        await service.wait(owner["turn_id"])
        nested = service.create(
            owner["agent_id"],
            str(tmp_path),
            {"name": "nested"},
            2,
            "spawn",
            notify=False,
            title="测试任务",
        )
        await service.wait(nested["turn_id"])
        job = jobs[0]
        process = (
            background_manager._handles[job.task_id].process if kind == "bash" else None
        )
        get_task_registry().notification_queue.enqueue(
            "stale notice", nested["agent_id"]
        )
        # Same order as delete_session: stop direct tasks, then recurse into owned instances.
        await bg_process.cancel_thread_bg_tasks("root")
        await service.delete_owned("root")
        assert process is None or process.returncode is not None
        assert job.status == TaskStatus.FAILED
        assert job.async_task is None or job.async_task.done()
        assert not service.store.agent_ids()
        assert not get_task_registry().notification_queue.has_for(nested["agent_id"])
        assert prompts == [
            "parent",
            "spawn",
        ]  # Cancellation must not wake a deleted owner.
    finally:
        await service.close()


@pytest.mark.parametrize("queued_started", [False, True])
async def test_stopping_queued_turn_preserves_current_and_later_turns(
    tmp_path, queued_started
):
    entered, release = asyncio.Event(), asyncio.Event()
    prompts = []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            prompts.append(turn.prompt)
            if turn.prompt == "first":
                entered.set()
                await release.wait()
            return turn.prompt

        async def close(self):
            pass

    service = SubagentService(tmp_path / "sessions.db", lambda _: Worker())
    try:
        first = service.create(
            "root",
            str(tmp_path),
            {"name": "worker"},
            1,
            "first",
            notify=False,
            title="测试任务",
        )
        await entered.wait()
        queued = service.message("root", first["agent_id"], "skip", notify=False)
        last = service.message("root", first["agent_id"], "last", notify=False)
        if queued_started:
            await asyncio.sleep(0)
        assert await bg_process.cancel_background_task(queued["turn_id"])
        assert (await service.wait(queued["turn_id"]))["status"] == "cancelled"
        assert service.receipt(first["turn_id"])["status"] == "running"
        release.set()
        assert (await service.wait(first["turn_id"]))["status"] == "completed"
        assert (await service.wait(last["turn_id"]))["output"] == "last"
        assert prompts == ["first", "last"]
    finally:
        await service.close()


@pytest.mark.parametrize(
    "crash_point", ["finished", "enqueued", "acknowledged", "shutdown"]
)
async def test_restart_routes_completed_nested_result_exactly_once(
    tmp_path, crash_point
):
    path = tmp_path / "sessions.db"
    store = SubagentStore(path)
    owner = store.create(
        "root",
        str(tmp_path),
        {"name": "owner"},
        1,
        "",
        "default",
        "parent",
        False,
        title="测试任务",
    )
    store.finish(owner.turn_id, "completed", "ready")
    nested = store.create(
        owner.agent_id,
        str(tmp_path),
        {"name": "nested"},
        2,
        "",
        "default",
        "nested",
        True,
        title="测试任务",
    )
    store.finish(nested.turn_id, "completed", "nested result")
    if crash_point != "finished":
        delivery = store.pending_deliveries(owner.agent_id)[0]
        store.submit(
            owner.agent_id,
            result_notification(delivery),
            True,
            "return:" + nested.turn_id,
        )
    if crash_point in ("acknowledged", "shutdown"):
        store.acknowledge(owner.agent_id, [nested.turn_id])
    store.close()
    prompts = []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            assert agent.agent_id == owner.agent_id
            prompts.append(turn.prompt)
            return "processed"

        async def close(self):
            pass

    service = SubagentService(path, lambda _: Worker())
    if crash_point == "shutdown":
        service.start()
        await (
            service.close()
        )  # Stop before the queued notification's first coroutine step.
        assert prompts == []
        service = SubagentService(path, lambda _: Worker())
    try:
        service.list_agents("root")
        assert (
            prompts == [] and not service._turn_tasks
        )  # Reading metadata never restores a process.
        manual = service.message("root", owner.agent_id, "continue", notify=False)
        await service.wait(manual["turn_id"])
        assert (
            len(prompts) == 2
            and "nested result" in prompts[0]
            and prompts[1] == "continue"
        )
        assert not service.store.has_deliveries(owner.agent_id)
        assert len(service.store.pending_deliveries("root")) == 1
    finally:
        await service.close()


@pytest.mark.parametrize("activation", ["startup", "message"])
@pytest.mark.parametrize(
    "older_source", ["background", "return_pending", "return_acknowledged"]
)
async def test_restart_preserves_fifo_with_queued_note_and_unforwarded_result(
    tmp_path, caplog, activation, older_source
):
    path = tmp_path / "sessions.db"
    store = SubagentStore(path)
    owner = store.create(
        "root",
        str(tmp_path),
        {"name": "owner"},
        1,
        "",
        "default",
        "parent",
        False,
        title="测试任务",
    )
    store.finish(owner.turn_id, "completed", "ready")

    def completed_result(prompt):
        nested = store.create(
            owner.agent_id,
            str(tmp_path),
            {"name": "nested"},
            2,
            "",
            "default",
            prompt,
            True,
            title="测试任务",
        )
        store.finish(nested.turn_id, "completed", prompt)
        return next(
            delivery
            for delivery in store.pending_deliveries(owner.agent_id)
            if delivery["turn_id"] == nested.turn_id
        )

    older_prompt, older_key = "older background result", "background:older-job"
    if older_source != "background":
        older_delivery = completed_result("older nested result")
        older_prompt = result_notification(older_delivery)
        older_key = "return:" + older_delivery["delivery_id"]
    store.submit(owner.agent_id, older_prompt, True, older_key)
    if older_source == "return_acknowledged":
        store.acknowledge(owner.agent_id, [older_delivery["delivery_id"]])
    newer_delivery = completed_result("newer nested result")
    store.close()
    received = []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            assert agent.agent_id == owner.agent_id
            received.append((turn.ordinal, turn.prompt))
            return f"processed {turn.ordinal}"

        async def close(self):
            pass

    service = SubagentService(path, lambda _: Worker())
    try:
        if activation == "startup":
            service.start()
        manual = service.message("root", owner.agent_id, "continue", notify=False)
        await asyncio.wait_for(service.wait(manual["turn_id"]), 3)
        service.start()  # Activation is idempotent, including pending old deliveries.
        assert received == [
            (2, older_prompt),
            (3, result_notification(newer_delivery)),
            (4, "continue"),
        ]
        assert not service.store.has_deliveries(owner.agent_id)
        assert len(service.store.pending_deliveries("root")) == 2
        assert service.list_agents("root")[0]["turn_count"] == 4
    finally:
        await service.close()
    await asyncio.sleep(0)  # Run any completion callbacks scheduled before shutdown.
    assert "Cannot operate on a closed database" not in caplog.text


async def test_stopping_running_turn_allows_next_round_to_proceed(tmp_path):
    entered = asyncio.Event()

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            if turn.prompt == "first":
                entered.set()
                await asyncio.Event().wait()
            return "next completed"

        async def close(self):
            pass

    service = SubagentService(tmp_path / "sessions.db", lambda _: Worker())
    try:
        first = service.create(
            "root",
            str(tmp_path),
            {"name": "worker"},
            1,
            "first",
            notify=False,
            title="测试任务",
        )
        await entered.wait()
        second = service.message("root", first["agent_id"], "second", notify=False)
        assert await bg_process.cancel_background_task(first["turn_id"])
        assert (await service.wait(first["turn_id"]))["status"] == "cancelled"
        assert (await service.wait(second["turn_id"]))["output"] == "next completed"
    finally:
        await service.close()


async def test_running_notification_is_not_replayed_but_preserves_its_payload(tmp_path):
    path = tmp_path / "sessions.db"
    store = SubagentStore(path)
    owner = store.create(
        "root",
        str(tmp_path),
        {"name": "owner"},
        1,
        "",
        "default",
        "parent",
        False,
        title="测试任务",
    )
    store.finish(owner.turn_id, "completed")
    note = store.submit(
        owner.agent_id, "important completed result", True, "background:job"
    )
    store.start(note.turn_id)
    store.close()
    service = SubagentService(
        path, lambda _: pytest.fail("Interrupted work cannot replay")
    )
    try:
        service.start()
        receipt = service.store.pending_deliveries("root")[0]
        assert (
            receipt["status"] == "interrupted"
            and receipt["output"] == "important completed result"
        )
        assert not service._turn_tasks
    finally:
        await service.close()


@pytest.mark.parametrize(
    "chunk,expected",
    [
        (
            AIMessageChunk(
                content="", additional_kwargs={"reasoning_content": "deepseek thought"}
            ),
            "deepseek thought",
        ),
        (
            AIMessageChunk(
                content=[
                    {"type": "thinking", "thinking": "one"},
                    {"type": "thinking", "thinking": "two"},
                    {"type": "text", "text": "answer"},
                ]
            ),
            "onetwo",
        ),
    ],
)
async def test_reasoning_deltas_are_persisted_in_subagent_history(
    tmp_path, chunk, expected
):
    service = SubagentService(tmp_path / "sessions.db")
    try:
        turn = service.store.create(
            "root",
            str(tmp_path),
            {"name": "worker"},
            1,
            "",
            "default",
            "work",
            False,
            title="测试任务",
        )
        writer = TimelineWriter(service, turn.agent_id, turn.turn_id)
        translate_lumi_event(
            {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "CallModel"},
                "run_id": "model",
                "parent_ids": ["graph"],
                "data": {"chunk": chunk},
            },
            writer.emit,
        )
        writer.flush()
        events = service.history("root", turn.agent_id)["events"]
        thought = next(e for e in events if e["type"] == "thinking.delta")
        assert thought["payload"]["text"] == expected
        assert thought["payload"]["parent_run_ids"] == ["graph"]
    finally:
        await service.close()


async def test_memory_checkpoint_mode_is_removed():
    with pytest.raises(ValidationError):
        AgentsConfig(checkpoint="memory")
    with pytest.raises(ValueError, match="memory"):
        await create_checkpointer("memory")
    assert (
        await create_checkpointer(None) is None
    )  # Temporary workflow execution stays stateless.


async def test_deleting_parent_cleans_persistent_child_history(
    tmp_path, isolated_config
):
    service = SubagentService(tmp_path / "sessions.db")
    turn = service.store.create(
        "root",
        str(tmp_path),
        {"name": "worker"},
        1,
        "",
        "default",
        "work",
        False,
        title="测试任务",
    )
    service.store.finish(turn.turn_id, "completed", "ready")
    parent = LumiAgent(await create_checkpointer("sqlite"))
    child = LumiAgent(await create_checkpointer("sqlite"))
    config = {"configurable": {"thread_id": "root"}}
    child_config = {"configurable": {"thread_id": turn.agent_id}}
    try:
        await parent.graph.aupdate_state(
            config, {"messages": [HumanMessage("parent")]}, as_node="OfflineFlush"
        )
        await child.graph.aupdate_state(
            child_config,
            {"messages": [HumanMessage("child history")]},
            as_node="OfflineFlush",
        )
        bridge = AgentBridge()
        bridge._agent, bridge._config = parent, config
        with patch(
            "lumi.agents.runtime.subagents.subagent_service_if_started",
            return_value=service,
        ):
            await bridge.delete_thread("root")
        assert not (await child.graph.aget_state(child_config)).values
        assert not (await parent.graph.aget_state(config)).values
        assert service.store.agent(turn.agent_id) is None
    finally:
        await child.aclose()
        await parent.aclose()
        await service.close()

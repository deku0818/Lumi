"""Persistent identity, FIFO turns, ownership, recovery and durable return contracts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from lumi.agents.runtime.bg_tasks import get_task_registry
from lumi.agents.runtime.subagents.service import SubagentService
from lumi.agents.tools.providers.agent import agent_create, agent_list, agent_message
from lumi.sessions.subagents import SubagentStore


class EchoExecutor:
    def __init__(self):
        self.prompts = []
        self.workspaces = []
        self.closed = False

    async def run(self, agent, turn, emit, save_handle):
        self.prompts.append(turn.prompt)
        self.workspaces.append(agent.workspace)
        save_handle({"session_id": agent.handle.get("session_id", agent.agent_id)})
        emit("tool.start", {"name": "read", "tool_call_id": "read-1"})
        emit(
            "tool.complete",
            {"name": "read", "tool_call_id": "read-1", "output": "file"},
        )
        emit("message.delta", {"text": turn.prompt, "parent_run_ids": ["native"]})
        await asyncio.sleep(0)
        return " / ".join(self.prompts)

    async def close(self):
        self.closed = True


@pytest_asyncio.fixture
async def service(tmp_path):
    runtime = SubagentService(tmp_path / "subagents.db", lambda _: EchoExecutor())
    yield runtime
    await runtime.close()


def create(service, tmp_path, prompt="one", **kwargs):
    return service.create(
        "parent",
        str(tmp_path),
        {
            "name": "worker",
            "executor": "lumi",
            "system_prompt": "role",
        },
        1,
        prompt,
        **kwargs,
    )


async def test_fifo_stable_identity_and_durable_history(service, tmp_path):
    first = create(service, tmp_path, request_key="create:1")
    retry = create(service, tmp_path, request_key="create:1")
    assert first == retry
    second = service.message("parent", first["agent_id"], "two")
    assert first["turn_id"] != second["turn_id"]
    results = await asyncio.gather(
        service.wait(first["turn_id"]), service.wait(second["turn_id"])
    )
    assert [r["output"] for r in results] == ["one", "one / two"]
    assert results[0]["agent_id"] == results[1]["agent_id"]
    history = service.history("parent", first["agent_id"], limit=2)
    remaining = service.history(
        "parent", first["agent_id"], after=history["cursor"], limit=100
    )
    events = history["events"] + remaining["events"]
    assert len({e["seq"] for e in events}) == len(events)
    assert sum(e["type"] == "turn.complete" for e in events) == 2
    assert any(e["payload"].get("parent_run_ids") == ["native"] for e in events)
    assert (
        service.store.agent(first["agent_id"]).handle["session_id"] == first["agent_id"]
    )
    entries = service.list_agents("parent")
    assert len(entries) == 1 and entries[0]["turn_count"] == 2
    assert entries[0]["status"] == "idle"
    assert len(service.store.pending_deliveries("parent")) == 2
    assert not get_task_registry().notification_queue.has_for("parent")
    assert get_task_registry().get(second["turn_id"]).progress["tools_done"] == 1


async def test_sync_returns_once_and_metadata_does_not_start_executor(
    service, tmp_path
):
    receipt = create(service, tmp_path, notify=False)
    assert (await service.wait(receipt["turn_id"]))["output"] == "one"
    assert not service.store.has_deliveries("parent")
    await service.close()
    calls = []
    reopened = SubagentService(
        tmp_path / "subagents.db", lambda kind: calls.append(kind)
    )
    try:
        assert reopened.list_agents("parent")[0]["agent_id"] == receipt["agent_id"]
        assert reopened.history("parent", receipt["agent_id"])["events"]
        assert calls == []
    finally:
        await reopened.close()


async def test_session_ownership_and_idempotency(service, tmp_path):
    first = create(service, tmp_path, request_key="key")
    with pytest.raises(ValueError, match="创建请求"):
        create(service, tmp_path, "different", request_key="key")
    for operation in (
        lambda: service.message("another", first["agent_id"], "bad"),
        lambda: service.history("another", first["agent_id"]),
    ):
        with pytest.raises(ValueError):
            operation()
    assert service.list_agents("another") == []
    await service.wait(first["turn_id"])


async def test_independent_instances_run_concurrently(tmp_path):
    started = set()
    both_started = asyncio.Event()

    class Rendezvous(EchoExecutor):
        async def run(self, agent, turn, emit, save_handle):
            started.add(agent.agent_id)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 2)
            return turn.prompt

    service = SubagentService(tmp_path / "parallel.db", lambda _: Rendezvous())
    try:
        a, b = create(service, tmp_path), create(service, tmp_path, "two")
        assert all(
            r["status"] == "completed"
            for r in await asyncio.gather(
                service.wait(a["turn_id"]), service.wait(b["turn_id"])
            )
        )
    finally:
        await service.close()


@pytest.mark.parametrize("before_start", [True, False])
async def test_cancellation_settles_active_and_queued_turns(tmp_path, before_start):
    started = asyncio.Event()

    class Waiting(EchoExecutor):
        async def run(self, *args):
            started.set()
            await asyncio.Event().wait()

    service = SubagentService(tmp_path / "cancel.db", lambda _: Waiting())
    try:
        first = create(service, tmp_path)
        second = service.message("parent", first["agent_id"], "queued")
        if not before_start:
            await asyncio.wait_for(started.wait(), 2)
        await service.stop("parent", first["agent_id"])
        assert (await service.wait(first["turn_id"]))["status"] == "cancelled"
        assert (await service.wait(second["turn_id"]))["status"] == "cancelled"
    finally:
        await service.close()


async def test_restart_never_replays_unfinished_work(tmp_path):
    path = tmp_path / "restart.db"
    store = SubagentStore(path)
    running = store.create(
        "parent", str(tmp_path), {"name": "test"}, 1, "", "default", "one", True
    )
    store.start(running.turn_id)
    queued = store.submit(running.agent_id, "two", True)
    store.save_handle(running.agent_id, {"thread_id": "native-42"})
    store.close()
    calls = []
    service = SubagentService(path, lambda kind: calls.append(kind))
    try:
        assert service.receipt(running.turn_id)["status"] == "interrupted"
        assert service.receipt(queued.turn_id)["status"] == "cancelled"
        assert service.store.agent(running.agent_id).handle == {
            "thread_id": "native-42"
        }
        assert len(service.store.pending_deliveries("parent")) == 2
        assert calls == []
    finally:
        await service.close()


async def test_second_owner_cannot_recover_live_runtime(service, tmp_path):
    with pytest.raises(RuntimeError, match="已有后端"):
        SubagentService(tmp_path / "subagents.db")


def runtime(tmp_path, depth=0):
    return SimpleNamespace(
        config={"configurable": {"thread_id": "parent"}},
        tool_call_id="c1",
        state={"depth": depth},
        context=SimpleNamespace(
            project_dir=tmp_path,
            permission_engine=None,
            env_extra="",
            tool_mode="default",
        ),
    )


@pytest.mark.parametrize(
    "name,kind", [(None, "lumi"), ("codex", "codex"), ("claude", "claude_code")]
)
async def test_tool_selects_name_without_markdown(
    service, tmp_path, monkeypatch, name, kind
):
    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    monkeypatch.setattr(
        "lumi.agents.core.preprocessing.agent_detector.AgentChangeDetector.get_instance",
        lambda *_: pytest.fail("Native/default execution must not load role files"),
    )
    rt = runtime(tmp_path)
    created = json.loads(
        await agent_create.coroutine(
            name=name, prompt="one", runtime=rt, run_in_background=False
        )
    )
    assert created["status"] == "completed"
    rt.tool_call_id = "c2"
    continued = json.loads(
        await agent_message.coroutine(
            agent_id=created["agent_id"],
            prompt="two",
            runtime=rt,
            run_in_background=False,
        )
    )
    assert continued["output"] == "one / two"
    listing = json.loads(await agent_list.coroutine(runtime=rt))["agents"]
    assert listing[0]["executor"] == kind
    assert listing[0]["name"] == (name or "lumi")
    assert listing[0]["cwd"] == str(tmp_path)
    assert service.store.agent(created["agent_id"]).depth == 1
    assert not service.store.has_deliveries("parent")


@pytest.mark.parametrize("name", [None, "codex", "claude"])
@pytest.mark.parametrize("directory_mode", ["inherited", "relative", "absolute"])
async def test_instance_cwd_survives_parent_directory_change(
    service, tmp_path, monkeypatch, name, directory_mode
):
    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    working_dir = tmp_path
    cwd = None
    if directory_mode != "inherited":
        working_dir = tmp_path / "other-project"
        working_dir.mkdir()
        cwd = "other-project" if directory_mode == "relative" else str(working_dir)
    rt = runtime(tmp_path)
    first = json.loads(
        await agent_create.coroutine(
            name=name,
            cwd=cwd,
            prompt="one",
            runtime=rt,
            run_in_background=False,
        )
    )
    assert first["status"] == "completed"
    agent_id = first["agent_id"]
    native_handle = service.store.agent(agent_id).handle
    rt.context.project_dir = tmp_path.parent
    rt.tool_call_id = "c2"
    listing = json.loads(await agent_list.coroutine(runtime=rt))["agents"]
    assert listing[0]["agent_id"] == agent_id and listing[0]["cwd"] == str(working_dir)
    second = json.loads(
        await agent_message.coroutine(
            agent_id=agent_id, prompt="two", runtime=rt, run_in_background=False
        )
    )
    assert second["output"] == "one / two"
    assert service._executors[agent_id].workspaces == [str(working_dir)] * 2
    assert service.store.agent(agent_id).handle == native_handle
    # A different conversation in the same directory still cannot inspect or continue it.
    rt.config["configurable"]["thread_id"] = "another-parent"
    rt.context.project_dir = working_dir
    assert json.loads(await agent_list.coroutine(runtime=rt))["agents"] == []
    assert "error" in json.loads(
        await agent_message.coroutine(agent_id=agent_id, prompt="bad", runtime=rt)
    )


async def test_list_and_message_need_only_session_identity(
    service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    first = create(service, tmp_path)
    await service.wait(first["turn_id"])
    rt = runtime(tmp_path)
    rt.context = None
    assert (
        json.loads(await agent_list.coroutine(runtime=rt))["agents"][0]["agent_id"]
        == first["agent_id"]
    )
    result = json.loads(
        await agent_message.coroutine(
            agent_id=first["agent_id"],
            prompt="two",
            runtime=rt,
            run_in_background=False,
        )
    )
    assert result["output"] == "one / two"


async def test_invalid_cwd_does_not_create_an_instance(service, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    result = json.loads(
        await agent_create.coroutine(
            name="claude", cwd="missing", prompt="work", runtime=runtime(tmp_path)
        )
    )
    assert "工作目录不存在" in result["error"]
    assert service.list_agents("parent") == []
    assert not service._executors


async def test_session_lists_instances_in_multiple_directories_after_restart(
    service, tmp_path
):
    a = create(service, tmp_path, notify=False)
    b = create(service, tmp_path.parent, notify=False)
    await asyncio.gather(service.wait(a["turn_id"]), service.wait(b["turn_id"]))
    await service.close()
    reopened = SubagentService(
        tmp_path / "subagents.db",
        lambda _: pytest.fail("Listing cannot start executors"),
    )
    try:
        listing = reopened.list_agents("parent")
        assert {item["cwd"] for item in listing} == {
            str(tmp_path),
            str(tmp_path.parent),
        }
        assert len(listing) == 2
    finally:
        await reopened.close()


async def test_lumi_role_selection_still_uses_parent_configuration(
    service, tmp_path, monkeypatch
):
    from lumi.agents.tools.loader import AgentConfig

    profile = AgentConfig(
        name="translator", description="translate", system_prompt="Translate carefully"
    )
    project_dirs = []

    def detector(project_dir):
        project_dirs.append(project_dir)
        return SimpleNamespace(peek=lambda: [profile])

    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    monkeypatch.setattr(
        "lumi.agents.core.preprocessing.agent_detector.AgentChangeDetector.get_instance",
        detector,
    )
    result = json.loads(
        await agent_create.coroutine(
            name="translator",
            cwd=str(tmp_path.parent),
            prompt="translate",
            runtime=runtime(tmp_path),
            run_in_background=False,
        )
    )
    agent = service.store.agent(result["agent_id"])
    assert project_dirs == [tmp_path]
    assert (
        agent.executor == "lumi"
        and agent.definition["system_prompt"] == "Translate carefully"
    )
    assert agent.workspace == str(tmp_path.parent)


def test_create_schema_uses_name_and_optional_cwd():
    from lumi.agents.tools.providers.agent import AgentCreateInput

    assert set(AgentCreateInput.model_fields) == {
        "name",
        "cwd",
        "prompt",
        "run_in_background",
    }


async def test_nested_subagent_results_are_queued_for_parent(service, tmp_path):
    parent = create(service, tmp_path, notify=False)
    await service.wait(parent["turn_id"])
    nested = service.create(
        parent["agent_id"], str(tmp_path), {"name": "nested"}, 2, "work"
    )
    await service.wait(nested["turn_id"])
    # Completion commits the parent's follow-up before exposing the nested result.
    followup = service.store.request("return:" + nested["turn_id"])
    assert followup is not None
    await service.wait(followup.turn_id)
    assert not service.store.has_deliveries(parent["agent_id"])
    assert service.store.has_deliveries("parent")


async def test_parent_deletion_retires_instances_and_durable_receipts(
    service, tmp_path, caplog
):
    first = create(service, tmp_path)
    await service.wait(first["turn_id"])
    executor = service._executors[first["agent_id"]]
    removed = await service.delete_owned("parent")
    assert removed == [first["agent_id"]]
    assert executor.closed
    assert service.list_agents("parent") == []
    assert not service.store.has_deliveries("parent")
    assert not (service.output_dir / f"{first['turn_id']}.txt").exists()
    await asyncio.sleep(0)  # Late callbacks must not read already-deleted turns.
    assert "子代理轮次不存在" not in caplog.text


def test_tools_registered_without_old_alias():
    from lumi.agents.tools.providers import agent
    from lumi.agents.tools.registry import _collect_tools_from_module

    assert {t.name for t in _collect_tools_from_module(agent)} == {
        "agent_create",
        "agent_list",
        "agent_message",
    }

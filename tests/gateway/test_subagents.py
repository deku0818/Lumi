"""Read-only observations and checkpoint-backed result receipt acknowledgement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lumi.agents.runtime.subagents.service import SubagentService
from lumi.gateway.bridge.core import AgentBridge
from lumi.gateway.subagents import (
    list_subagents,
    observe_subagent,
    stop_subagent,
    subagent_history,
    unobserve_subagent,
)


async def test_stop_instance_cancels_running_and_queued_turns_only(
    tmp_path, monkeypatch
):
    import asyncio

    started = set()
    executed = []

    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            started.add(agent.agent_id)
            executed.append(turn.prompt)
            if turn.prompt != "continue after stop":
                await asyncio.Event().wait()
            return "continued"

        async def close(self):
            pass

    service = SubagentService(tmp_path / "subagents.db", lambda _: Worker())
    monkeypatch.setattr("lumi.gateway.subagents.get_subagent_service", lambda: service)
    session = SimpleNamespace(current_thread_id="parent")
    try:
        first = service.create(
            "parent", str(tmp_path), {"name": "worker"}, 1, "running", notify=False
        )
        second = service.message(
            "parent", first["agent_id"], "queued one", notify=False
        )
        third = service.message("parent", first["agent_id"], "queued two", notify=False)
        other = service.create(
            "parent", str(tmp_path), {"name": "other"}, 1, "other running", notify=False
        )
        async with asyncio.timeout(3):
            while len(started) < 2:
                await asyncio.sleep(0)
        with pytest.raises(ValueError):
            await stop_subagent(
                SimpleNamespace(current_thread_id="another"),
                {"agent_id": first["agent_id"]},
            )
        assert service.store.turn(first["turn_id"]).status == "running"
        assert await stop_subagent(session, {"agent_id": first["agent_id"]}) == {
            "stopped": True
        }
        for turn in (first, second, third):
            assert (await service.wait(turn["turn_id"]))["status"] == "cancelled"
        assert service.store.turn(other["turn_id"]).status == "running"
        assert executed == ["running", "other running"]
        resumed = service.message(
            "parent", first["agent_id"], "continue after stop", notify=False
        )
        assert (await service.wait(resumed["turn_id"]))["output"] == "continued"
        assert service.store.turn(other["turn_id"]).status == "running"
    finally:
        await service.close()


async def test_observation_is_scoped_subscribed_before_snapshot_and_persisted(
    tmp_path, monkeypatch
):
    service = SubagentService(tmp_path / "subagents.db")
    first = service.store.create(
        "parent",
        str(tmp_path),
        {"name": "codex", "executor": "codex"},
        1,
        "",
        "default",
        "question",
        True,
    )
    channel = object()
    order = []
    hub = SimpleNamespace(add_observer=lambda aid, ch: order.append((aid, ch)))
    session = SimpleNamespace(
        current_thread_id="parent",
        _bridge=SimpleNamespace(workspace_dir=str(tmp_path)),
        _hub=hub,
        _channel=channel,
    )
    monkeypatch.setattr("lumi.gateway.subagents.get_subagent_service", lambda: service)
    original = service.history

    def history(*args, **kwargs):
        assert order == [(first.agent_id, channel)]
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "history", history)
    try:
        session._bridge.workspace_dir = str(tmp_path.parent)
        result = await observe_subagent(session, {"agent_id": first.agent_id})
        assert result["events"][0]["payload"]["text"] == "question"
        assert (await list_subagents(session, {}))["agents"][0]["executor"] == "codex"
        assert (await list_subagents(session, {}))["agents"][0]["cwd"] == str(tmp_path)
        assert (
            service._executors == {}
        )  # Observation cannot start native CLI processes.
        other = SimpleNamespace(
            **{
                **vars(session),
                "current_thread_id": "other",
                "_bridge": SimpleNamespace(workspace_dir=str(tmp_path)),
            }
        )
        with pytest.raises(ValueError):
            await subagent_history(other, {"agent_id": first.agent_id})
        with pytest.raises(ValueError):
            await observe_subagent(other, {"agent_id": first.agent_id})
    finally:
        await service.close()


async def test_receipt_ack_requires_committed_parent_checkpoint(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage

    service = SubagentService(tmp_path / "subagents.db")
    monkeypatch.setattr(
        "lumi.agents.runtime.subagents.subagent_service_if_started", lambda: service
    )
    turn = service.store.create(
        "parent", str(tmp_path), {"name": "test"}, 1, "", "default", "question", True
    )
    service.store.finish(turn.turn_id, "completed", "answer")
    bridge = AgentBridge()
    bridge._config = {"configurable": {"thread_id": "parent"}}
    committed = []
    historical = []

    async def history(_):
        for snapshot in historical:
            yield snapshot

    graph = SimpleNamespace(
        aget_state=AsyncMock(
            side_effect=lambda _: SimpleNamespace(values={"messages": committed})
        ),
        aget_state_history=history,
    )
    bridge._agent = SimpleNamespace(graph=graph)
    try:
        assert bridge.has_notifications("parent")
        notifications = bridge.drain_notifications("parent")
        assert len(notifications) == 1 and "answer" in notifications[0]
        await bridge.reconcile_subagent_deliveries()
        assert service.store.has_deliveries(
            "parent"
        )  # Presented alone is not durable delivery.
        # Crash/restart may compact the delivered input out of the latest checkpoint.
        historical.append(
            SimpleNamespace(
                values={
                    "messages": [
                        HumanMessage(
                            content="receipt",
                            additional_kwargs={
                                "lumi": {"subagent_deliveries": [turn.turn_id]}
                            },
                        )
                    ]
                }
            )
        )
        await bridge.reconcile_subagent_deliveries()
        assert not bridge.has_notifications("parent")
        assert bridge.drain_notifications("parent") == []
    finally:
        await service.close()


@pytest.mark.parametrize("agent_id", ["subagent_1", "a7k3m9q2"])
def test_subagent_cannot_be_bound_as_a_human_chat(agent_id):
    with pytest.raises(ValueError, match="只读"):
        AgentBridge().switch_thread(agent_id)


async def test_instance_metadata_distinguishes_active_round_from_queued_round(
    tmp_path, monkeypatch
):
    service = SubagentService(tmp_path / "subagents.db")
    monkeypatch.setattr("lumi.gateway.subagents.get_subagent_service", lambda: service)
    first = service.store.create(
        "parent",
        str(tmp_path),
        {"name": "codex", "executor": "codex"},
        1,
        "",
        "default",
        "Review\nthis project",
        False,
    )
    service.store.start(first.turn_id)
    second = service.store.submit(first.agent_id, "follow up", False)
    native_id = "00000000-0000-4000-8000-000000000001"
    service.store.save_handle(first.agent_id, {"thread_id": native_id})
    session = SimpleNamespace(current_thread_id="parent")
    try:
        summary = (await list_subagents(session, {}))["agents"][0]
        assert summary["title"] == "Review this project"
        assert summary["active_turn_id"] == first.turn_id
        assert summary["active_ordinal"] == 1 and summary["turn_count"] == 2
        assert (
            summary["last_turn_id"] == second.turn_id and summary["queued_count"] == 1
        )
        assert (await subagent_history(session, {"agent_id": first.agent_id}))[
            "native_session_id"
        ] == native_id
    finally:
        await service.close()


async def test_unobserve_only_removes_that_instance_and_connection(
    tmp_path, monkeypatch
):
    import asyncio

    from lumi.gateway.broadcast import BroadcastHub

    service = SubagentService(tmp_path / "subagents.db")
    monkeypatch.setattr("lumi.gateway.subagents.get_subagent_service", lambda: service)
    first = service.store.create(
        "parent", str(tmp_path), {"name": "codex"}, 1, "", "default", "first", False
    )
    second = service.store.create(
        "parent", str(tmp_path), {"name": "lumi"}, 1, "", "default", "second", False
    )

    class Channel:
        async def send(self, frame):
            pass

    channel, other = Channel(), Channel()
    hub = BroadcastHub()
    hub.add_observer(first.agent_id, channel)
    hub.add_observer(first.agent_id, other)
    hub.add_observer(second.agent_id, channel)
    session = SimpleNamespace(current_thread_id="parent", _hub=hub, _channel=channel)
    try:
        await unobserve_subagent(session, {"agent_id": first.agent_id})
        assert hub.has_observers(first.agent_id) and hub.has_observers(second.agent_id)
        assert channel not in hub._observers[first.agent_id]
        session.current_thread_id = "another"
        with pytest.raises(ValueError):
            await unobserve_subagent(session, {"agent_id": second.agent_id})
        assert channel in hub._observers[second.agent_id]
    finally:
        hub.remove_observer_channel(channel)
        hub.remove_observer_channel(other)
        await asyncio.sleep(0)
        await service.close()

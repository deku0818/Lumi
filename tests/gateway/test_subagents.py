"""Read-only observations and checkpoint-backed result receipt acknowledgement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lumi.agents.runtime.subagents.service import SubagentService
from lumi.gateway.bridge.core import AgentBridge
from lumi.gateway.subagents import list_subagents, observe_subagent, subagent_history


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

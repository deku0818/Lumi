"""Short IDs remain durable, collision-checked identities, including existing sessions."""

import re

import pytest

from lumi.agents.runtime.subagents.service import SubagentService
from lumi.sessions.subagents import SubagentStore
from lumi.utils.thread_id import is_subagent_thread


def create(store, tmp_path, request_key=None):
    return store.create(
        "parent",
        str(tmp_path),
        {"name": "codex", "executor": "codex"},
        1,
        "",
        "default",
        "one",
        False,
        request_key,
    )


def test_id_collisions_regenerate_without_overwriting_existing_rows(
    tmp_path, monkeypatch
):
    # Second creation first collides with both the existing instance and its first turn.
    characters = iter(
        "1111111" + "2222222" + "1111111" + "3333333" + "2222222" + "4444444"
    )
    monkeypatch.setattr("lumi.sessions.subagents.choice", lambda _: next(characters))
    store = SubagentStore(tmp_path / "ids.db")
    try:
        first = create(store, tmp_path, "first")
        second = create(store, tmp_path, "second")
        assert (first.agent_id, first.turn_id) == ("a1111111", "t2222222")
        assert (second.agent_id, second.turn_id) == ("a3333333", "t4444444")
        assert create(store, tmp_path, "first").turn_id == first.turn_id
        assert len(store.list_agents("parent")) == 2
        assert store.turn(first.turn_id).agent_id == first.agent_id
    finally:
        store.close()


async def test_short_ids_and_native_handle_survive_restart_and_continuation(tmp_path):
    path = tmp_path / "ids.db"
    store = SubagentStore(path)
    first = create(store, tmp_path)
    native_id = "01a15851-ff0c-7a71-ac82-19b7090a76a6"
    store.save_handle(first.agent_id, {"thread_id": native_id})
    store.finish(first.turn_id, "completed", "one")
    store.close()

    class Resumed:
        async def run(self, agent, turn, emit, save_handle):
            assert agent.agent_id == first.agent_id
            assert agent.handle == {"thread_id": native_id}
            return "two"

        async def close(self):
            pass

    service = SubagentService(path, lambda _: Resumed())
    try:
        assert re.fullmatch(r"a[a-z0-9]{7}", first.agent_id)
        assert re.fullmatch(r"t[a-z0-9]{7}", first.turn_id)
        second = service.message("parent", first.agent_id, "two", notify=False)
        result = await service.wait(second["turn_id"])
        assert result["agent_id"] == first.agent_id
        assert re.fullmatch(r"t[a-z0-9]{7}", result["turn_id"])
        assert result["task_id"] == result["turn_id"] != first.turn_id
        assert result["output"] == "two"
        assert service.list_agents("parent")[0]["last_turn_id"] == result["turn_id"]
        assert service.store.agent(first.agent_id).handle == {"thread_id": native_id}
    finally:
        await service.close()


async def test_previously_persisted_long_ids_still_resume(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    store = SubagentStore(path)
    legacy_agent = "subagent_46aa8ac61ce14845a6a05efc00f87c24"
    legacy_turn = "bg_c3ad8d61ec97403798cfe267dd6b677c"
    native_handle = {"thread_id": "01a15851-ff0c-7a71-ac82-19b7090a76a6"}
    with monkeypatch.context() as previous_version:
        previous_version.setattr(
            store,
            "_new_id",
            lambda table, column, prefix: (
                legacy_agent if prefix == "a" else legacy_turn
            ),
        )
        first = create(store, tmp_path)
    store.save_handle(first.agent_id, native_handle)
    store.finish(first.turn_id, "completed", "one")
    store.close()

    class Existing:
        async def run(self, agent, turn, emit, save_handle):
            assert agent.handle == native_handle
            return "continued"

        async def close(self):
            pass

    service = SubagentService(path, lambda _: Existing())
    try:
        assert service.list_agents("parent")[0]["agent_id"] == legacy_agent
        second = service.message("parent", legacy_agent, "two", notify=False)
        assert (await service.wait(second["turn_id"]))["output"] == "continued"
        assert re.fullmatch(r"t[a-z0-9]{7}", second["turn_id"])
        assert service.store.turn(legacy_turn).status == "completed"
        assert service.store.agent(legacy_agent).handle == native_handle
    finally:
        await service.close()


@pytest.mark.parametrize(
    "thread_id,expected",
    [
        ("a7k3m9q2", True),
        ("subagent_46aa8ac61ce14845a6a05efc00f87c24", True),
        ("t4n8p6r1", False),
        ("t-46aa8ac61ce14845a6a05efc00f87c24", False),
        ("abc", False),
        ("a12345678", False),
    ],
)
def test_subagent_thread_namespace(thread_id, expected):
    assert is_subagent_thread(thread_id) is expected

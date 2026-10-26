"""Native availability, opt-in, shared catalog and existing-session continuation."""

import json
from types import SimpleNamespace

import pytest
from conftest import rpc
from langchain_core.messages import HumanMessage

from lumi.agents.core.hooks.schema import HookContext
from lumi.agents.core.meta_message import extract_text_content
from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector
from lumi.agents.core.preprocessing.context_inject import context_inject_hook
from lumi.agents.runtime.subagents import availability
from lumi.agents.runtime.subagents.service import SubagentService
from lumi.agents.tools.providers.agent import agent_create, agent_message
from lumi.gateway import project_config, toolbox
from lumi.gateway.session import _RPC_HANDLERS
from lumi.utils.config import GlobalConfigManager, user_store


@pytest.fixture
def probes(monkeypatch, tmp_path):
    availability._cache.clear()
    statuses = {
        "codex": (True, "Logged in using ChatGPT"),
        "claude": (True, '{"loggedIn":true,"email":"private@example.invalid"}'),
    }
    calls = []
    monkeypatch.setattr(availability, "resolve_cli", lambda name: f"/native/{name}")

    def run(command, timeout=30):
        calls.append(command)
        if command[-1] == "--version":
            return True, "2.3.4"
        assert command[1:] in (["login", "status"], ["auth", "status"])
        return statuses[command[0].split("/")[-1]]

    monkeypatch.setattr(availability, "run_cli", run)
    events = []

    async def send_event(kind, payload):
        events.append((kind, payload))

    hub = SimpleNamespace(send_event=send_event)

    async def request(params):
        return await _RPC_HANDLERS["set_project_agent"](
            SimpleNamespace(_hub=hub), params
        )

    async def set_agent(name, enabled, project=tmp_path):
        return await request({"path": str(project), "name": name, "enabled": enabled})

    yield SimpleNamespace(
        statuses=statuses,
        calls=calls,
        events=events,
        project=tmp_path,
        set_agent=set_agent,
        request=request,
    )
    availability._cache.clear()


@pytest.mark.parametrize(
    "name,ok,output,state",
    [
        ("codex", True, "Logged in using ChatGPT", "ready"),
        ("codex", False, "Not logged in", "login"),
        ("codex", False, "temporary failure", "error"),
        ("claude", True, '{"loggedIn":true}', "ready"),
        ("claude", False, '{"loggedIn":false}', "login"),
        ("claude", True, "not json", "error"),
        ("claude", False, '{"loggedIn":true}', "error"),
    ],
)
def test_read_only_auth_probes(probes, name, ok, output, state):
    probes.statuses[name] = ok, output
    check = availability.check_agent(name)
    assert check.state == state
    assert check.version == "2.3.4"
    assert len(probes.calls) == 2


def test_cached_checks_refresh_expire_and_relocate(probes, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(availability.time, "monotonic", lambda: clock[0])
    assert availability.check_agent("codex").state == "ready"
    assert availability.check_agent("codex").state == "ready"
    assert len(probes.calls) == 2
    probes.statuses["codex"] = False, "Not logged in"
    assert availability.check_agent("codex", refresh=True).state == "login"
    assert len(probes.calls) == 4
    clock[0] += 31
    assert availability.check_agent("codex").state == "login"
    assert len(probes.calls) == 6
    monkeypatch.setattr(availability, "resolve_cli", lambda _: "/new/codex")
    assert availability.check_agent("codex").path == "/new/codex"
    assert len(probes.calls) == 8


def test_missing_program_and_version_failure(probes, monkeypatch):
    def missing(_):
        raise RuntimeError("not found")

    monkeypatch.setattr(availability, "resolve_cli", missing)
    assert availability.check_agent("codex").state == "missing"
    assert not probes.calls
    monkeypatch.setattr(availability, "resolve_cli", lambda _: "/native/codex")
    monkeypatch.setattr(availability, "run_cli", lambda *_, **__: (False, "failed"))
    assert availability.check_agent("codex").state == "error"


def test_disabled_default_never_spawns_a_probe(probes, tmp_path):
    assert availability.enabled_agents(probes.project) == {}
    assert availability.available_agents(probes.project) == []
    assert all(
        agent.executor == "lumi"
        for agent in AgentChangeDetector.get_instance(tmp_path).peek()
    )
    assert not probes.calls


async def test_enable_rechecks_auth_and_preserves_other_settings(probes):
    config = GlobalConfigManager.load()
    config.unattended_tool_mode = "privileged"
    config.checkpoint_dir = "/kept"
    GlobalConfigManager.save(config)
    result = availability.check_agent("claude")
    assert result.state == "ready"
    assert "private@example" not in repr(result)
    probes.statuses["claude"] = False, '{"loggedIn":false}'
    with pytest.raises(ValueError, match="未登录"):
        await probes.set_agent("claude", True)
    assert not availability.enabled_agents(probes.project).get("claude")
    probes.statuses["claude"] = True, '{"loggedIn":true}'
    await probes.set_agent("claude", True)
    saved = GlobalConfigManager.load()
    assert availability.enabled_agents(probes.project) == {"claude": True}
    assert (
        saved.checkpoint_dir == "/kept" and saved.unattended_tool_mode == "privileged"
    )
    probes.calls.clear()
    await probes.set_agent("claude", False)
    assert not probes.calls  # Turning off must not wait for or depend on a working CLI.
    assert probes.events[-1] == (
        "agents.changed",
        {"project": str(probes.project.resolve())},
    )


@pytest.mark.parametrize(
    "params",
    [{"name": "other", "enabled": True}, {"name": "codex", "enabled": "false"}],
)
async def test_invalid_settings_rejected(probes, params):
    with pytest.raises(ValueError):
        await probes.request({"path": str(probes.project), **params})
    assert availability.enabled_agents(probes.project) == {}


async def test_project_and_reminder_share_catalog_and_emit_removal(
    probes, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "lumi.agents.core.preprocessing.context_inject.get_project_directory",
        lambda: tmp_path,
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            project_dir=tmp_path,
            tools=[agent_create],
            env_extra="",
            memory_enabled=False,
        )
    )

    async def inject(messages):
        command = await context_inject_hook(
            HookContext(
                event="UserPromptSubmit",
                state={"messages": messages},
                config={},
                runtime=runtime,
            )
        )
        return command.update["messages"][0]

    await probes.set_agent("codex", True)
    first = await inject([HumanMessage(content="start", id="one")])
    assert "- codex:" in extract_text_content(first.content)
    project_agents = project_config.overview(tmp_path)["agents"]
    catalog = AgentChangeDetector.get_instance(tmp_path).peek()
    assert {agent["name"] for agent in project_agents} == {
        agent.name for agent in catalog
    }
    native = next(agent for agent in project_agents if agent["name"] == "codex")
    assert native["source"] == "native" and native["executor"] == "codex"
    detail = project_config.read_resource(tmp_path, "agent", "codex")
    assert detail["native"] and detail["content"] == "" and not detail.get("path")
    await probes.set_agent("codex", False)
    second = await inject([first, HumanMessage(content="again", id="two")])
    assert "codex" not in second.additional_kwargs["ctx_digest"]["agents"]
    assert "移除:\n- codex" in extract_text_content(second.content)
    assert "- codex:" in extract_text_content(first.content)  # History remains intact.
    assert not any(
        agent["name"] == "codex"
        for agent in project_config.overview(tmp_path)["agents"]
    )
    assert not (tmp_path / ".lumi/agents/codex.md").exists()


@pytest.mark.parametrize("name", ["codex", "claude"])
async def test_disabled_creation_rejected_but_existing_instances_continue(
    probes, tmp_path, monkeypatch, name
):
    class Worker:
        async def run(self, agent, turn, emit, save_handle):
            save_handle({"session_id": "same-native-session"})
            return turn.prompt

        async def close(self):
            pass

    service = SubagentService(tmp_path / "sessions.db", lambda _: Worker())
    monkeypatch.setattr(
        "lumi.agents.tools.providers.agent.get_subagent_service", lambda: service
    )
    runtime = SimpleNamespace(
        config={"configurable": {"thread_id": "parent"}},
        tool_call_id="one",
        state={},
        context=SimpleNamespace(
            project_dir=tmp_path,
            permission_engine=None,
            env_extra="",
            tool_mode="default",
        ),
    )
    try:
        denied = json.loads(
            await agent_create.coroutine(
                name=name, prompt="first", runtime=runtime, title="测试任务"
            )
        )
        assert "error" in denied and service.list_agents("parent") == []
        await probes.set_agent(name, True)
        first = json.loads(
            await agent_create.coroutine(
                name=name,
                prompt="first",
                runtime=runtime,
                run_in_background=False,
                title="测试任务",
            )
        )
        assert first["status"] == "completed"
        handle = service.store.agent(first["agent_id"]).handle
        await probes.set_agent(name, False)
        runtime.tool_call_id = "two"
        assert "error" in json.loads(
            await agent_create.coroutine(
                name=name, prompt="new", runtime=runtime, title="测试任务"
            )
        )
        resumed = json.loads(
            await agent_message.coroutine(
                agent_id=first["agent_id"],
                prompt="continued",
                runtime=runtime,
                run_in_background=False,
            )
        )
        assert resumed["status"] == "completed" and resumed["output"] == "continued"
        assert service.store.agent(first["agent_id"]).handle == handle
        assert len(service.list_agents("parent")) == 1
    finally:
        await service.close()


def test_native_names_cannot_be_overridden_by_markdown(probes, tmp_path):
    directory = tmp_path / ".lumi/agents"
    directory.mkdir(parents=True)
    path = directory / "codex.md"
    path.write_text("---\nname: codex\ndescription: fake native\n---\nrole")
    assert "codex" not in {
        agent.name for agent in AgentChangeDetector.get_instance(tmp_path).peek()
    }
    for action in (
        lambda: project_config.write_resource(
            tmp_path, "agent", "codex", path.read_text()
        ),
        lambda: project_config.copy_builtin(tmp_path, "agent", "codex"),
        lambda: project_config.delete_resource(tmp_path, "agent", "codex"),
    ):
        with pytest.raises(ValueError, match="项目的子 Agent"):
            action()
    assert path.exists()


async def test_project_preferences_are_independent_and_normalize_paths(
    probes, tmp_path
):
    other = tmp_path / "other"
    other.mkdir()
    await probes.set_agent("codex", True)
    assert [agent.name for agent in availability.available_agents(tmp_path)] == [
        "codex"
    ]
    assert availability.enabled_agents(other) == {}
    assert availability.available_agents(other) == []
    alias = tmp_path / "alias"
    alias.symlink_to(other, target_is_directory=True)
    await probes.set_agent("claude", True, alias)
    assert availability.enabled_agents(other) == {"claude": True}
    assert availability.enabled_agents(tmp_path) == {"codex": True}
    assert probes.events[-1][1] == {"project": str(other.resolve())}
    await probes.set_agent("codex", False)
    assert [agent.name for agent in availability.available_agents(other)] == ["claude"]


async def test_enable_preserves_project_config_and_ignores_old_global_flag(probes):
    user_store.write_section("settings", {"external_agents": {"codex": True}})
    assert availability.available_agents(probes.project) == []
    assert availability.available_agents(None) == []
    path = probes.project / ".lumi/agents.json"
    path.parent.mkdir()
    path.write_text('{"kept":"value"}')
    await probes.set_agent("claude", True)
    assert json.loads(path.read_text()) == {"kept": "value", "claude": True}


@pytest.mark.parametrize("path", [None, "", "__missing_directory__"])
async def test_missing_project_never_writes_a_default_location(probes, path):
    with pytest.raises(ValueError, match="项目目录"):
        await probes.request({"path": path, "name": "codex", "enabled": True})
    assert not probes.calls


def test_broken_project_preferences_fail_closed(probes):
    path = probes.project / ".lumi/agents.json"
    path.parent.mkdir()
    for contents in ("invalid json", "[]", '{"codex":"true","claude":0}'):
        path.write_text(contents)
        assert availability.available_agents(probes.project) == []
    assert not probes.calls


async def test_native_programs_are_not_auto_installed(probes):
    assert not set(availability.NATIVE_AGENTS) & set(toolbox.ALL_TOOLS)
    with pytest.raises(ValueError):
        await rpc("env_install", {"target": "codex"})


def test_tool_descriptions_do_not_embed_provider_catalog():
    for tool in (agent_create, agent_message):
        text = tool.description + json.dumps(tool.args_schema.model_json_schema())
        assert "codex" not in text.lower() and "claude" not in text.lower()

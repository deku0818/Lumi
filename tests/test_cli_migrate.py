"""临时命令的真实旧数据迁移：只检查、无备份、幂等及迁移后正常读写。"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated, TypedDict
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Overwrite
from typer.testing import CliRunner

from lumi._migrate import migrate, migrate_config
from lumi._migrate.checkpoints import (
    Transformer,
    migration_serde,
    rename_thread,
    rewrite_references,
)
from lumi.agents.core.graph import LumiAgent, _checkpoint_serde
from lumi.agents.core.meta_message import HISTORY_KEY
from lumi.cli import app
from lumi.gateway.session import _history_items
from lumi.models.manager import create_llm
from lumi.models.reasoning import ORIGIN, prepare_history
from lumi.sessions.history import HistoryReader


class OldState(TypedDict):
    messages: Annotated[list, add_messages]
    todos: list


async def seed_checkpoints(path: Path, thread: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with migration_serde() as serde:
        Todo = sys.modules["lumi.agents.tools.providers.todo"].Todo
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            saver.serde = serde
            builder = StateGraph(OldState)
            builder.add_node("OfflineFlush", lambda state: {})
            builder.add_node("HumanApproval", lambda state: {})
            builder.add_edge(START, "HumanApproval")
            builder.add_edge("HumanApproval", END)
            builder.add_edge("OfflineFlush", END)
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": thread}}
            messages = [
                HumanMessage(content="原始问题", id="human"),
                AIMessage(
                    content="",
                    id="call",
                    tool_calls=[
                        {"id": "files", "name": "present_files", "args": {"files": []}}
                    ],
                ),
                ToolMessage(
                    content="[]",
                    name="present_files",
                    tool_call_id="files",
                    id="result",
                ),
                AIMessage(content="完整回答", id="answer"),
            ]
            await graph.aupdate_state(
                config,
                {
                    "messages": messages,
                    "todos": [Todo(content="旧任务", status="pending")],
                },
                as_node="OfflineFlush",
            )
            carrier = HumanMessage(
                content="<compact>旧摘要</compact>",
                id="compact",
                additional_kwargs={"lumi": {"items": []}},
            )
            await graph.aupdate_state(
                config,
                {
                    "messages": Overwrite(
                        [carrier, messages[-1].model_copy(update={"id": "reattached"})]
                    )
                },
                as_node="OfflineFlush",
            )


@pytest.fixture
def old_home(tmp_path, monkeypatch):
    from lumi.utils.config import user_store
    from lumi.utils.config.global_manager import GlobalConfigManager

    monkeypatch.setenv("LUMI_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(user_store, "CONFIG_FILE", tmp_path / "lumi.json")
    monkeypatch.setattr(
        GlobalConfigManager,
        "load",
        classmethod(
            lambda cls: type(
                "Settings",
                (),
                {"get_checkpoint_dir": lambda self: tmp_path / "checkpoints"},
            )()
        ),
    )
    config = {
        "providers": {
            "profiles": [
                {
                    "id": "p",
                    "name": "我的连接",
                    "api_key": "not-a-real-secret",
                    "base_url": "https://example.invalid",
                    "model": "qwen3",
                }
            ],
            "active": "p",
        },
        "channels": {
            "feishu": {"app_id": "old-app", "workspace": "/project", "enabled": False}
        },
    }
    (tmp_path / "lumi.json").write_text(json.dumps(config), encoding="utf-8")
    folder = tmp_path / "memory" / "projects" / "project"
    folder.mkdir(parents=True)
    (folder / "MEMORY.md").write_text(
        "- [记录](topic.md) [project · 2026-01-02] — 结论\n"
    )
    (folder / "topic.md").write_text("---\nname: topic\n---\n记忆正文\n")
    return tmp_path


def test_config_migration_splits_connections_and_keeps_pointers():
    old = {
        "providers": {
            "profiles": [
                {
                    "id": "p",
                    "name": "连接",
                    "models": ["qwen3", "claude-sonnet"],
                    "effort": {"qwen3": "on", "claude-sonnet": "high"},
                }
            ],
            "active": {"provider": "p", "model": "claude-sonnet"},
        }
    }
    updated, _, mapping = migrate_config(old)
    profiles = updated["providers"]["profiles"]
    assert [(p["protocol"], p["models"]) for p in profiles] == [
        ("openai", ["qwen3"]),
        ("anthropic", ["claude-sonnet"]),
    ]
    assert updated["providers"]["active"]["provider"] == mapping["p", "claude-sonnet"]
    assert migrate_config(updated)[0] == updated
    assert "protocol" not in old["providers"]["profiles"][0]


def test_check_is_read_only_and_apply_is_idempotent_without_backups(old_home):
    root = old_home
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    runner = CliRunner()
    checked = runner.invoke(app, ["migrate", "--check"])
    assert checked.exit_code == 0, checked.output
    assert "待迁移" in checked.output
    assert {
        p: p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.suffix in (".json", ".md")
    } == before
    applied = runner.invoke(app, ["migrate", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "未创建备份" in applied.output
    config = json.loads((root / "lumi.json").read_text())
    assert config["providers"]["profiles"][0]["models"] == ["qwen3"]
    assert config["providers"]["profiles"][0]["compatibility"] == "qwen"
    assert isinstance(config["channels"]["feishu"], list)
    assert "legacy_threads" not in config["channels"]["feishu"][0]
    assert "[project ·" not in (root / "memory/projects/project/MEMORY.md").read_text()
    assert "date: 2026-01-02" in (root / "memory/projects/project/topic.md").read_text()
    again = runner.invoke(app, ["migrate", "--apply"])
    assert again.exit_code == 0, again.output
    assert "0 个配置/元数据文件，0 个记忆文件，0 条数据库记录" in again.output
    assert not any("backup" in p.name or ".bak" in p.name for p in root.rglob("*"))
    assert "lumi.agents.tools.providers.todo" not in sys.modules


@pytest.mark.parametrize("encrypted", [False, True])
async def test_checkpoint_history_and_todo_migrate_to_current_graph(
    old_home, monkeypatch, encrypted
):
    if encrypted:
        monkeypatch.setenv("LUMI_CHECKPOINT_AES_KEY", "0123456789abcdef")
    else:
        monkeypatch.delenv("LUMI_CHECKPOINT_AES_KEY", raising=False)
    directory = old_home / "checkpoints"
    await seed_checkpoints(directory / "checkpoints.db", "feishu-chat")
    (directory / "session_meta.json").write_text(
        json.dumps(
            {"feishu-chat": {"title": "保留标题", "fork_uploads": ["/uploads/a"]}}
        )
    )
    before = (directory / "checkpoints.db").read_bytes()
    await migrate(apply=False)
    assert (directory / "checkpoints.db").read_bytes() == before
    await migrate(apply=True)
    config = json.loads((old_home / "lumi.json").read_text())
    bot = config["channels"]["feishu"][0]["id"]
    thread = f"feishu-{bot}-chat"
    metadata = json.loads((directory / "session_meta.json").read_text())
    assert metadata[thread] == {"title": "保留标题", "shared_uploads": ["/uploads/a"]}
    async with AsyncSqliteSaver.from_conn_string(
        str(directory / "checkpoints.db")
    ) as saver:
        saver.serde = _checkpoint_serde()
        graph = LumiAgent(checkpointer=saver).graph
        state = await graph.aget_state({"configurable": {"thread_id": thread}})
        assert "todos" not in state.values
        assert HISTORY_KEY in state.values["messages"][0].additional_kwargs
        reader = HistoryReader()
        first = await reader.page(graph, thread, _history_items)
        assert [i["text"] for i in first["items"]] == ["完整回答"]
        earlier = await reader.page(
            graph, thread, _history_items, cursor=first["next_cursor"]
        )
        assert any(i.get("name") == "artifacts" for i in earlier["items"])
        assert any(i.get("text") == "原始问题" for i in earlier["items"])
    second = await migrate(apply=True)
    assert "0 条数据库记录" in second[0]


def test_cli_migration_options_are_explicit(old_home):
    runner = CliRunner()
    assert runner.invoke(app, ["migrate"]).exit_code == 0
    assert runner.invoke(app, ["migrate", "--check", "--apply"]).exit_code == 2


async def test_invalid_sidecar_is_rejected_before_database_changes(old_home):
    path = old_home / "checkpoints" / "checkpoints.db"
    await seed_checkpoints(path, "feishu-chat")
    (path.parent / "session_meta.json").write_text("{invalid")
    before = path.read_bytes()
    config_before = (old_home / "lumi.json").read_bytes()
    with pytest.raises(json.JSONDecodeError):
        await migrate(apply=True)
    assert path.read_bytes() == before
    assert (old_home / "lumi.json").read_bytes() == config_before


def test_normal_cli_does_not_import_migration_modules():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import lumi.cli; assert not any(n.startswith('lumi._migrate') for n in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_migration_assigns_ids_without_changing_user_text():
    def rename(value):
        return rename_thread(value, "bot", {"bot"})

    convert = Transformer(rename, {}, {})
    old = [HumanMessage(content="same"), HumanMessage(content="same")]
    current = convert.value(old, "thread")
    assert current[0].id and current[1].id and current[0].id != current[1].id
    assert [m.content for m in current] == ["same", "same"]
    assert convert.value(current, "thread") == current
    assert all(m.id is None for m in old)
    assert convert.value({"structured_output": {"todos": ["业务数据"]}}, "thread") == {
        "structured_output": {"todos": ["业务数据"]}
    }


def test_approval_node_versions_merge_without_reopening_finished_work():
    convert = Transformer(str, {}, {})
    old = {
        "v": 4,
        "channel_values": {"todos": []},
        "channel_versions": {
            "branch:to:HumanApproval": "003",
            "branch:to:AutoClassify": "001",
        },
        "versions_seen": {
            "HumanApproval": {"branch:to:HumanApproval": "003"},
            "AutoClassify": {"branch:to:AutoClassify": "001"},
        },
    }
    updated = convert.value(old, "thread")
    assert updated["channel_versions"] == {"branch:to:ToolExecutor": "003"}
    assert updated["versions_seen"] == {
        "ToolExecutor": {"branch:to:ToolExecutor": "003"}
    }
    assert "todos" not in updated["channel_values"]


def test_reference_collision_is_reported_instead_of_overwriting_metadata():
    old = {
        "feishu-chat": {"title": "旧记录"},
        "feishu-bot-chat": {"title": "另一条记录"},
    }

    def rename(value):
        return rename_thread(value, "bot", {"bot"})

    with pytest.raises(ValueError, match="重复键"):
        rewrite_references(old, rename)
    assert old["feishu-chat"]["title"] == "旧记录"


async def test_pending_approval_node_migrates_to_current_executor(old_home):
    directory = old_home / "checkpoints"
    directory.mkdir()
    async with AsyncSqliteSaver.from_conn_string(
        str(directory / "checkpoints.db")
    ) as saver:
        builder = StateGraph(OldState)
        builder.add_node(
            "CallModel",
            lambda state: {
                "messages": [
                    AIMessage(
                        content="待执行",
                        tool_calls=[
                            {"id": "read", "name": "read", "args": {"file_path": "x"}}
                        ],
                    )
                ]
            },
        )
        builder.add_node("HumanApproval", lambda state: {})
        builder.add_edge(START, "CallModel")
        builder.add_edge("CallModel", "HumanApproval")
        builder.add_edge("HumanApproval", END)
        old = builder.compile(checkpointer=saver, interrupt_before=["HumanApproval"])
        config = {"configurable": {"thread_id": "pending"}}
        await old.ainvoke(
            {"messages": [HumanMessage(content="读取文件")], "todos": []}, config
        )
    await migrate(apply=True)
    async with AsyncSqliteSaver.from_conn_string(
        str(directory / "checkpoints.db")
    ) as saver:
        saver.serde = _checkpoint_serde()
        current = LumiAgent(checkpointer=saver).graph
        state = await current.aget_state(config)
        assert state.next == ("ToolExecutor",)
        assert state.values["messages"][-1].content == "待执行"
        assert "todos" not in state.values


async def test_local_subagent_schema_and_references_migrate_once(old_home):
    from lumi.sessions.subagents import SubagentStore

    directory = old_home / "checkpoints"
    directory.mkdir()
    agent = "subagent_46aa8ac61ce14845a6a05efc00f87c24"
    path = directory / "subagents.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE subagents (agent_id TEXT PRIMARY KEY,parent_id TEXT,workspace TEXT,name TEXT,executor TEXT,definition TEXT,depth INTEGER,env_extra TEXT,tool_mode TEXT,handle TEXT,created_at REAL)"
        )
        db.execute(
            "INSERT INTO subagents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                agent,
                "feishu-chat",
                "/project",
                "worker",
                "lumi",
                "{}",
                1,
                "",
                "auto",
                json.dumps({"thread_id": agent}),
                0,
            ),
        )
        db.execute("CREATE TABLE dream_cursor (value TEXT)")
    await migrate(apply=False)
    with sqlite3.connect(path) as db:
        assert "title" not in {
            row[1] for row in db.execute("PRAGMA table_info(subagents)")
        }
    await migrate(apply=True)
    expected = rename_thread(agent, "", set())
    store = SubagentStore(path)
    try:
        saved = store.agent(expected)
        assert saved.title == ""
        assert saved.handle["thread_id"] == expected
        assert (
            saved.parent_id.startswith("feishu-") and saved.parent_id != "feishu-chat"
        )
        assert (
            store.conn.execute(
                "SELECT name FROM sqlite_master WHERE name='dream_cursor'"
            ).fetchone()
            is None
        )
    finally:
        store.close()
    assert "0 条数据库记录" in (await migrate(apply=True))[0]


@pytest.mark.parametrize(
    "failed_file", ["session_meta.json", "session_forks.json", "MEMORY.md", "lumi.json"]
)
async def test_failed_apply_preserves_config_until_references_are_written(
    old_home, failed_file
):
    from lumi._migrate import atomic_write_json, atomic_write_text

    path = old_home / "lumi.json"
    original = json.loads(path.read_text())
    profile = original["providers"]["profiles"][0]
    profile.pop("model")
    profile["models"] = ["qwen3", "claude-sonnet"]
    original["providers"]["active"] = {"provider": "p", "model": "claude-sonnet"}
    path.write_text(json.dumps(original))
    config_bytes = path.read_bytes()
    expected, _, mapping = migrate_config(original)
    thread = f"feishu-{expected['channels']['feishu'][0]['id']}-chat"
    directory = old_home / "checkpoints"
    await seed_checkpoints(directory / "checkpoints.db", "feishu-chat")
    meta = directory / "session_meta.json"
    meta.write_text(
        json.dumps(
            {
                "feishu-chat": {
                    "title": "保留标题",
                    "model_provider": "p",
                    "model": "claude-sonnet",
                    "fork_uploads": ["/uploads/a"],
                }
            }
        )
    )
    forks = directory / "session_forks.json"
    forks.write_text(json.dumps({"feishu-chat": {"sequence": 2}}))

    def fail_json(target, value, **kwargs):
        if Path(target).name == failed_file:
            raise PermissionError("模拟写入失败")
        return atomic_write_json(target, value, **kwargs)

    def fail_text(target, content, **kwargs):
        if target.name == failed_file:
            raise PermissionError("模拟写入失败")
        return atomic_write_text(target, content, **kwargs)

    with (
        patch("lumi._migrate.atomic_write_json", side_effect=fail_json),
        patch("lumi._migrate.atomic_write_text", side_effect=fail_text),
    ):
        with pytest.raises(PermissionError, match="模拟写入失败"):
            await migrate(apply=True)
    assert path.read_bytes() == config_bytes
    with sqlite3.connect(directory / "checkpoints.db") as db:
        assert {
            row[0] for row in db.execute("SELECT DISTINCT thread_id FROM checkpoints")
        } == {thread}
    await migrate(apply=True)
    assert json.loads(path.read_text()) == expected
    assert json.loads(meta.read_text()) == {
        thread: {
            "title": "保留标题",
            "model_provider": mapping["p", "claude-sonnet"],
            "model": "claude-sonnet",
            "shared_uploads": ["/uploads/a"],
        }
    }
    assert json.loads(forks.read_text()) == {thread: {"sequence": 2}}
    assert (
        "[project ·" not in (old_home / "memory/projects/project/MEMORY.md").read_text()
    )
    assert (
        "0 个配置/元数据文件，0 个记忆文件，0 条数据库记录"
        in (await migrate(apply=True))[0]
    )
    assert not any(
        "backup" in p.name or p.suffix == ".bak" for p in old_home.rglob("*")
    )


@pytest.mark.parametrize("use_responses", [False, True])
@pytest.mark.parametrize("explicit", [None, "openai", "responses"])
async def test_migration_keeps_effective_responses_api_selection(
    old_home, use_responses, explicit
):
    from lumi.utils.config import get_config

    (old_home / "config.json").write_text(
        json.dumps({"llm_params": {"openai": {"use_responses_api": use_responses}}})
    )
    path = old_home / "lumi.json"
    original = json.loads(path.read_text())
    profile = {"id": "p", "name": "连接", "models": ["gpt-5.2"]}
    if explicit:
        profile.update(protocol=explicit, compatibility="standard")
    original["providers"]["profiles"] = [profile]
    path.write_text(json.dumps(original))
    get_config(str(old_home))
    arguments = dict(
        model_name="gpt-5.2",
        api_key="test",
        base_url="https://example.invalid/v1",
        use_cache=False,
    )
    before = create_llm(
        **arguments,
        protocol=explicit or "",
        compatibility=profile.get("compatibility", ""),
    )
    await migrate(apply=True)
    saved = json.loads(path.read_text())["providers"]["profiles"][0]
    after = create_llm(
        **arguments, protocol=saved["protocol"], compatibility=saved["compatibility"]
    )
    assert after.lumi_origin[2] == before.lumi_origin[2]
    assert after.use_responses_api == before.use_responses_api
    assert ("input" in after._get_request_payload([HumanMessage("问题")])) is (
        before.use_responses_api is True
    )
    assert "0 个配置/元数据文件" in (await migrate(apply=True))[0]


@pytest.mark.parametrize(
    "model", ["custom-alias", "openrouter/custom-alias", "deepseek-v3"]
)
@pytest.mark.parametrize("mixed_profile", [False, True])
async def test_migration_keeps_existing_reasoning_wire_fields(
    old_home, model, mixed_profile
):
    from lumi.utils.config import get_config

    path = old_home / "lumi.json"
    original = json.loads(path.read_text())
    original["providers"]["profiles"] = [
        {
            "id": "p",
            "name": "连接",
            "models": ["qwen3", model] if mixed_profile else [model],
        }
    ]
    path.write_text(json.dumps(original))
    get_config(str(old_home))
    arguments = dict(
        model_name=model,
        api_key="test",
        base_url="https://example.invalid/v1",
        use_cache=False,
    )
    before = create_llm(**arguments, provider="p")
    messages = [
        HumanMessage("问题"),
        AIMessage(
            content="回答",
            additional_kwargs={
                "reasoning_content": "原始思考",
                "encrypted_content": "cipher",
            },
            response_metadata={ORIGIN: before.lumi_origin},
        ),
    ]
    expected = before._get_request_payload(
        prepare_history(messages, before.lumi_origin)
    )
    directory = old_home / "checkpoints"
    directory.mkdir()
    async with AsyncSqliteSaver.from_conn_string(
        str(directory / "checkpoints.db")
    ) as saver:
        await LumiAgent(checkpointer=saver).graph.aupdate_state(
            {"configurable": {"thread_id": "reasoning"}},
            {"messages": messages},
            as_node="OfflineFlush",
        )
    await migrate(apply=True)
    saved = next(
        p
        for p in json.loads(path.read_text())["providers"]["profiles"]
        if model in p["models"]
    )
    after = create_llm(
        **arguments,
        provider=saved["id"],
        protocol=saved["protocol"],
        compatibility=saved["compatibility"],
    )
    assert after.lumi_compatibility == before.lumi_compatibility
    async with AsyncSqliteSaver.from_conn_string(
        str(directory / "checkpoints.db")
    ) as saver:
        saver.serde = _checkpoint_serde()
        state = await LumiAgent(checkpointer=saver).graph.aget_state(
            {"configurable": {"thread_id": "reasoning"}}
        )
    assert (
        after._get_request_payload(
            prepare_history(state.values["messages"], after.lumi_origin)
        )
        == expected
    )
    assert "0 条数据库记录" in (await migrate(apply=True))[0]

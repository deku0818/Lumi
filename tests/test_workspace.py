"""项目目录上下文与跨目录文件操作。"""

import asyncio
from pathlib import Path

import pytest

from lumi.agents.core.preprocessing.system_info import collect_system_info
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.runtime.workspace import (
    get_project_directory,
    resolve_tool_path,
    set_project_source,
)
from lumi.agents.tools.providers.filesystem import LocalFilesystemBackend


def test_project_directory_defaults_to_cwd():
    assert get_project_directory() == Path.cwd().resolve()


def test_system_info_uses_session_project(project_tmp_dir):
    assert collect_system_info()["cwd"] == str(project_tmp_dir)


async def test_concurrent_runs_keep_their_project(tmp_path):
    async def run(project: Path):
        set_project_source(lambda: project)
        await asyncio.sleep(0)
        return resolve_tool_path("file.txt")

    a, b = tmp_path / "a", tmp_path / "b"
    assert await asyncio.gather(run(a), run(b)) == [a / "file.txt", b / "file.txt"]
    assert get_project_directory() == Path.cwd().resolve()


@pytest.mark.parametrize("with_engine", [False, True])
async def test_direct_graph_runs_use_their_own_project(
    tmp_path, monkeypatch, with_engine
):
    from langgraph.graph import END, START, StateGraph

    from lumi.agents.core.state import LumiAgentContext

    monkeypatch.chdir(tmp_path)

    async def write_file(state):
        result = await LocalFilesystemBackend().write("note.txt", "hello")
        assert result["error"] is None
        return {}

    builder = StateGraph(dict, context_schema=LumiAgentContext)
    builder.add_node("write", write_file)
    builder.add_edge(START, "write")
    builder.add_edge("write", END)
    graph = builder.compile()
    projects = [tmp_path / "a", tmp_path / "b"]
    contexts = [
        LumiAgentContext(
            project_dir=p,
            permission_engine=(
                PermissionEngine(p, user_config_dir=tmp_path / "home")
                if with_engine
                else None
            ),
        )
        for p in projects
    ]
    await asyncio.gather(*(graph.ainvoke({}, context=ctx) for ctx in contexts))
    assert all((p / "note.txt").read_text() == "hello" for p in projects)
    assert not (tmp_path / "note.txt").exists()


def test_project_source_follows_rebase(tmp_path):
    engine = PermissionEngine(tmp_path / "a", user_config_dir=tmp_path / "home")
    set_project_source(lambda: engine.project_dir)
    engine.rebase(tmp_path / "b")
    assert resolve_tool_path("file.txt") == tmp_path / "b" / "file.txt"


@pytest.mark.parametrize("path_kind", ["absolute", "parent", "symlink", "home"])
async def test_write_and_edit_outside_project(tmp_path, monkeypatch, path_kind):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    set_project_source(lambda: project)
    target = outside / "note.txt"
    if path_kind == "absolute":
        path = str(target)
    elif path_kind == "parent":
        path = "../outside/note.txt"
    elif path_kind == "symlink":
        (project / "link").symlink_to(outside, target_is_directory=True)
        path = "link/note.txt"
    else:
        monkeypatch.setenv("HOME", str(outside))
        path = "~/note.txt"
    backend = LocalFilesystemBackend()
    assert (await backend.write(path, "before"))["error"] is None
    assert target.read_text() == "before"
    assert (await backend.edit(path, "before", "after"))["error"] is None
    assert target.read_text() == "after"

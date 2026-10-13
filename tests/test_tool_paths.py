"""文件类工具的路径口径：展开 ``~``，相对路径基于会话项目根而非进程 cwd。

``lumi serve`` 是多项目网关、从不 chdir：按进程 cwd 解析时，read("src/a.py") 读不到项目
文件或读到别处同名文件，因此 read/write/edit 统一按项目解析，并展开 ``~``。
"""

from __future__ import annotations

import json

import pytest

from lumi.agents.tools.providers.artifacts import artifacts
from lumi.agents.tools.providers.filesystem import read


@pytest.fixture
def project(tmp_path, monkeypatch, project_tmp_dir):
    """会话项目与进程 cwd 分离；进程 cwd 与 HOME 各指向别处。"""
    (project_tmp_dir / "src").mkdir()
    (project_tmp_dir / "src" / "a.txt").write_text("in-project\n")
    elsewhere = tmp_path.parent / f"{tmp_path.name}-cwd"
    (elsewhere / "src").mkdir(parents=True)
    (elsewhere / "src" / "a.txt").write_text("wrong-dir\n")
    monkeypatch.chdir(elsewhere)
    home = tmp_path.parent / f"{tmp_path.name}-home"
    home.mkdir()
    (home / "notes.txt").write_text("from-home\n")
    monkeypatch.setenv("HOME", str(home))
    return project_tmp_dir


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "c1", "type": "tool_call"}


async def test_read_relative_and_home(project):
    rel = await read.ainvoke(_call("read", {"file_path": "src/a.txt"}))
    assert "in-project" in rel.content
    home = await read.ainvoke(_call("read", {"file_path": "~/notes.txt"}))
    assert "from-home" in home.content


def test_artifacts_relative_path(project):
    [entry] = json.loads(artifacts.invoke({"filepaths": ["src/a.txt"]}))
    assert entry["path"] == str(project / "src" / "a.txt")

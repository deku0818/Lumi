"""文件类工具的路径口径：展开 ``~``，相对路径基于会话项目根（主授权目录）而非进程 cwd。

``lumi serve`` 是多项目网关、从不 chdir：按进程 cwd 解析时，read("src/a.py") 读不到项目
文件或读到别处同名文件，glob 搜的是 serve 的启动目录；而 write/edit 走 validate_path 按
项目解析——同一轮里读写指向不同文件。文本 read 还不展开 ``~``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.tools.providers.artifacts import artifacts
from lumi.agents.tools.providers.filesystem import glob, grep, read


@pytest.fixture
def project(tmp_path, monkeypatch, authorized_tmp_dir):
    """会话项目 = 授权目录；进程 cwd 与 HOME 各指向别处。"""
    (authorized_tmp_dir / "src").mkdir()
    (authorized_tmp_dir / "src" / "a.txt").write_text("in-project\n")
    elsewhere = tmp_path.parent / f"{tmp_path.name}-cwd"
    (elsewhere / "src").mkdir(parents=True)
    (elsewhere / "src" / "a.txt").write_text("wrong-dir\n")
    monkeypatch.chdir(elsewhere)
    home = tmp_path.parent / f"{tmp_path.name}-home"
    home.mkdir()
    (home / "notes.txt").write_text("from-home\n")
    monkeypatch.setenv("HOME", str(home))
    return authorized_tmp_dir


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "c1", "type": "tool_call"}


async def test_read_relative_and_home(project):
    rel = await read.ainvoke(_call("read", {"file_path": "src/a.txt"}))
    assert "in-project" in rel.content
    home = await read.ainvoke(_call("read", {"file_path": "~/notes.txt"}))
    assert "from-home" in home.content


async def test_glob_and_grep_relative_path(project):
    found = await glob.ainvoke({"pattern": "*.txt", "path": "src"})
    assert str(project / "src" / "a.txt") in found
    hits = await grep.ainvoke({"pattern": "project", "path": "src"})
    assert str(project / "src" / "a.txt") in hits


def test_artifacts_relative_path(project):
    [entry] = json.loads(artifacts.invoke({"filepaths": ["src/a.txt"]}))
    assert entry["path"] == str(project / "src" / "a.txt")


def test_boundary_check_expands_home(project):
    # ~/x 曾被拼成 <项目>/~/x 判为界内，实际写到的却是家目录
    engine = PermissionEngine(project)
    violations = engine.get_boundary_violations("write", {"file_path": "~/x.txt"})
    assert violations == [str(Path.home() / "x.txt")]

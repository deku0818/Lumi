"""持久记忆 + 项目说明（LUMI.md）注入的纯函数测试。

覆盖：路径 sanitize / 边界判定、记忆行为说明组装、MEMORY.md 索引与 LUMI.md 加载、
路由免审批 carve-out、首条消息注入块。全部为纯字符串/路径断言，不执行真实工具。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.core.preprocessing.memory import format_memory_reminder
from lumi.agents.memory import (
    build_memory_instructions,
    is_memory_path,
    load_memory_index,
    load_project_doc,
    memory_dir,
    memory_entrypoint,
    paths,
)
from lumi.agents.memory.prompt import MAX_INDEX_LINES
from lumi.agents.permissions.routing import _is_memory_write, route_decision

_PROJ = Path("/Users/x/Cocoon/Lumi")


# === 路径与边界 ===


def test_sanitize_path_readable():
    """项目路径 → 可读的目录名（斜杠转横线，与 Claude Code 一致）。"""
    assert memory_dir(_PROJ).name == "-Users-x-Cocoon-Lumi"


def test_entrypoint_under_memory_dir():
    assert memory_entrypoint(_PROJ) == memory_dir(_PROJ) / "MEMORY.md"


def test_is_memory_path_inside():
    assert is_memory_path(str(memory_dir(_PROJ) / "user_role.md"), _PROJ) is True


def test_is_memory_path_outside():
    assert is_memory_path("/etc/passwd", _PROJ) is False


def test_is_memory_path_project_file_is_not_memory():
    """项目内的普通文件不算记忆路径（免审批只针对记忆目录）。"""
    assert is_memory_path(str(_PROJ / "foo.py"), _PROJ) is False


def test_is_memory_path_traversal_blocked():
    """.. 穿越逃出记忆目录后不再算记忆路径。"""
    escaped = str(memory_dir(_PROJ) / ".." / ".." / "evil.md")
    assert is_memory_path(escaped, _PROJ) is False


# === 行为说明 ===


def test_instructions_contain_taxonomy_and_dir():
    text = build_memory_instructions(memory_dir(_PROJ))
    for token in ("user", "feedback", "project", "reference", "MEMORY.md"):
        assert token in text
    assert str(memory_dir(_PROJ)) in text


# === MEMORY.md 索引加载 ===


def _point_memory_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(paths, "MEMORY_ROOT", root)


def test_load_memory_index_missing(tmp_path, monkeypatch):
    _point_memory_root(monkeypatch, tmp_path)
    assert load_memory_index(_PROJ) is None


def test_load_memory_index_reads_content(tmp_path, monkeypatch):
    _point_memory_root(monkeypatch, tmp_path)
    ep = memory_entrypoint(_PROJ)
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_text("- [角色](user_role.md) — 后端工程师\n", encoding="utf-8")
    assert "后端工程师" in load_memory_index(_PROJ)


def test_load_memory_index_truncates(tmp_path, monkeypatch):
    _point_memory_root(monkeypatch, tmp_path)
    ep = memory_entrypoint(_PROJ)
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_text(
        "\n".join(f"- line {i}" for i in range(MAX_INDEX_LINES + 50)), "utf-8"
    )
    out = load_memory_index(_PROJ)
    assert "仅加载了一部分" in out


# === LUMI.md 项目说明加载 ===


def test_load_project_doc_missing(tmp_path):
    assert load_project_doc(tmp_path) is None


def test_load_project_doc_reads(tmp_path):
    (tmp_path / "LUMI.md").write_text("本项目用 uv 管理依赖", encoding="utf-8")
    assert "uv" in load_project_doc(tmp_path)


# === 路由免审批 carve-out ===


def _write_tc(file_path: str) -> dict:
    return {"name": "write", "args": {"file_path": file_path, "content": "x"}}


def test_is_memory_write_true_for_memory_path():
    assert _is_memory_write(_write_tc(str(memory_dir(_PROJ) / "f.md")), _PROJ) is True


def test_is_memory_write_false_for_project_path():
    assert _is_memory_write(_write_tc(str(_PROJ / "f.py")), _PROJ) is False


def test_is_memory_write_false_for_non_edit_tool():
    assert _is_memory_write({"name": "bash", "args": {}}, _PROJ) is False


def test_route_memory_write_auto_allows(monkeypatch):
    """default 模式下，写记忆目录免审批直接 ToolExecutor（engine=None 也成立）。"""
    monkeypatch.setattr(
        "lumi.agents.permissions.routing.get_authorized_directory", lambda: _PROJ
    )
    tcs = [_write_tc(str(memory_dir(_PROJ) / "feedback_x.md"))]
    assert route_decision(tcs, "default", "normal", None) == "ToolExecutor"


def test_route_non_memory_write_still_evaluated(monkeypatch):
    """写项目外普通文件不走记忆 carve-out（engine=None → default 回退审批）。"""
    monkeypatch.setattr(
        "lumi.agents.permissions.routing.get_authorized_directory", lambda: _PROJ
    )
    tcs = [_write_tc("/tmp/evil.sh")]
    assert route_decision(tcs, "default", "normal", None) == "HumanApproval"


# === 首条消息注入块 ===


def test_reminder_none_when_empty(tmp_path, monkeypatch):
    """无 LUMI.md 且不含记忆 → 不注入（返回 None）。"""
    _point_memory_root(monkeypatch, tmp_path)
    assert format_memory_reminder(tmp_path, include_memory=False) is None


def test_reminder_includes_project_doc_for_subagent(tmp_path, monkeypatch):
    """子 agent（include_memory=False）仍注入 LUMI.md，但不含记忆索引。"""
    _point_memory_root(monkeypatch, tmp_path)
    (tmp_path / "LUMI.md").write_text("项目约定", encoding="utf-8")
    out = format_memory_reminder(tmp_path, include_memory=False)
    assert "项目约定" in out
    assert "持久记忆索引" not in out

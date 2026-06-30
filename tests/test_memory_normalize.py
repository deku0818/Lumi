"""normalize_memory_index 索引行兜底补全测试。"""

from __future__ import annotations

from lumi.agents.memory import paths as memory_paths
from lumi.agents.memory.normalize import normalize_memory_index
from lumi.agents.memory.paths import ensure_memory_dir, memory_dir, memory_entrypoint


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    proj.mkdir()
    ensure_memory_dir(proj)
    return proj


def _write_topic(proj, name, type_):
    (memory_dir(proj) / name).write_text(
        f"---\nname: {name}\ndescription: d\ntype: {type_}\n---\n正文", encoding="utf-8"
    )


def test_补全缺失的_type_日期(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "f.md", "feedback")
    memory_entrypoint(proj).write_text(
        "# 索引\n- [用 pnpm](f.md) — 包管理器\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    out = memory_entrypoint(proj).read_text()
    assert "[feedback · " in out and "— 包管理器" in out
    assert out.startswith("# 索引")  # 非指针行保留


def test_幂等(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "u.md", "user")
    memory_entrypoint(proj).write_text("- [角色](u.md) — 后端\n", encoding="utf-8")
    normalize_memory_index(proj)
    first = memory_entrypoint(proj).read_text()
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == first  # 第二次无变化


def test_已有合法标签保留(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "p.md", "project")
    line = "- [决策](p.md) [project · 2026-01-01] — 旧日期\n"
    memory_entrypoint(proj).write_text(line, encoding="utf-8")
    normalize_memory_index(proj)
    # 已带合法 [type · 日期] → 原样保留（不被 mtime 覆盖）
    assert "2026-01-01" in memory_entrypoint(proj).read_text()


def test_标题含方括号(tmp_path, monkeypatch):
    """标题含 ] / 嵌套 [] 时正则非贪婪应正确匹配（不在第一个 ] 处误断）。"""
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "b.md", "project")
    memory_entrypoint(proj).write_text(
        "- [修复 [bug] 的记录](b.md) — 钩子\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    out = memory_entrypoint(proj).read_text()
    assert "[project · " in out and "修复 [bug] 的记录" in out and "— 钩子" in out


def test_取不到_type_不强补(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)  # 不建 topic 文件
    memory_entrypoint(proj).write_text("- [缺失](missing.md) — x\n", encoding="utf-8")
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == "- [缺失](missing.md) — x\n"

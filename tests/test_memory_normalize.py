"""normalize_memory_index 索引行兜底规范化测试。"""

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


def _write_topic(proj, name, type_, date=None, prefix=""):
    date_line = f"date: {date}\n" if date else ""
    (memory_dir(proj) / name).write_text(
        f"{prefix}---\nname: {name}\ndescription: d\ntype: {type_}\n{date_line}---\n正文",
        encoding="utf-8",
    )


def test_新格式行补缺失的_date(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "n.md", "user")  # 无 date
    line = "- [角色](n.md) — 后端\n"
    memory_entrypoint(proj).write_text(line, encoding="utf-8")
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == line  # 索引行不动
    assert "date: " in (memory_dir(proj) / "n.md").read_text()  # mtime 近似日期已补


def test_frontmatter_已有_date_不覆盖(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "u.md", "user", date="2026-05-01")
    memory_entrypoint(proj).write_text(
        "- [角色](u.md) [user · 2026-01-01] — 后端\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    topic = (memory_dir(proj) / "u.md").read_text()
    assert "date: 2026-05-01" in topic and "2026-01-01" not in topic


def test_不碰记忆目录外的文件(tmp_path, monkeypatch):
    # 回归：索引里写绝对路径 / ../ 指针时，项目文件被当 topic 插入 date 行
    proj = _setup(tmp_path, monkeypatch)
    outside = proj / "docs" / "post.md"
    outside.parent.mkdir()
    body = "---\nname: post\ndescription: d\n---\n正文\n"
    outside.write_text(body, encoding="utf-8")
    rel = "../" * 10 + str(outside).lstrip("/")
    memory_entrypoint(proj).write_text(
        f"- [a]({outside})\n- [b]({rel})\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    assert outside.read_text(encoding="utf-8") == body

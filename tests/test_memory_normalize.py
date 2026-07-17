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


def test_剥掉_legacy_tag_并回填_frontmatter(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "p.md", "project")
    memory_entrypoint(proj).write_text(
        "# 索引\n- [决策](p.md) [project · 2026-01-01] — 结论\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    assert (
        memory_entrypoint(proj).read_text() == "# 索引\n- [决策](p.md) — 结论\n"
    )  # tag 剥掉、非指针行保留
    topic = (memory_dir(proj) / "p.md").read_text()
    assert "date: 2026-01-01" in topic  # 日期回填 frontmatter
    normalize_memory_index(proj)  # 幂等：第二遍无变化
    assert memory_entrypoint(proj).read_text() == "# 索引\n- [决策](p.md) — 结论\n"
    assert (memory_dir(proj) / "p.md").read_text() == topic


def test_剥掉纯日期_tag(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "f.md", "feedback")
    memory_entrypoint(proj).write_text(
        "- [用 pnpm](f.md) [2026-06-20] — 一律 pnpm\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == "- [用 pnpm](f.md) — 一律 pnpm\n"
    assert "date: 2026-06-20" in (memory_dir(proj) / "f.md").read_text()


def test_frontmatter_前有空行也插进_frontmatter_内(tmp_path, monkeypatch):
    """插入点与 parse_frontmatter 同一套 lstrip 规则，date 不会落到 frontmatter 之外。"""
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "p.md", "project", prefix="\n\n")
    memory_entrypoint(proj).write_text(
        "- [决策](p.md) [project · 2026-01-01] — 结论\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    from lumi.utils.config.manager import parse_frontmatter

    meta, _ = parse_frontmatter((memory_dir(proj) / "p.md").read_text())
    assert str(meta["date"]) == "2026-01-01" and meta["type"] == "project"


def test_回填不了则保留原行(tmp_path, monkeypatch):
    """topic 缺失或无 frontmatter 时日期无处安放，tag 不剥、信息不丢。"""
    proj = _setup(tmp_path, monkeypatch)
    (memory_dir(proj) / "bare.md").write_text(
        "没有 frontmatter 的正文", encoding="utf-8"
    )
    lines = (
        "- [缺失](missing.md) [project · 2026-01-01] — x\n"
        "- [裸文件](bare.md) [feedback · 2026-02-02] — y\n"
    )
    memory_entrypoint(proj).write_text(lines, encoding="utf-8")
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == lines
    assert (memory_dir(proj) / "bare.md").read_text() == "没有 frontmatter 的正文"


def test_非_legacy_方括号内容不碰(tmp_path, monkeypatch):
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "a.md", "project")
    _write_topic(proj, "rel.md", "project")
    lines = (
        "- [a](a.md) [补充](y.md) — z\n"
        "- [发布计划](rel.md) [截止 2026-09-01] — 按 v3 走\n"
    )
    memory_entrypoint(proj).write_text(lines, encoding="utf-8")
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == lines  # 正文方括号原样保留
    assert (
        "date:" not in (memory_dir(proj) / "rel.md").read_text()
    )  # 截止日期没被灌成写入日期


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


def test_标题含方括号(tmp_path, monkeypatch):
    """标题含 ] / 嵌套 [] 时正则非贪婪应正确匹配（不在第一个 ] 处误断）。"""
    proj = _setup(tmp_path, monkeypatch)
    _write_topic(proj, "b.md", "project")
    memory_entrypoint(proj).write_text(
        "- [修复 [bug] 的记录](b.md) [project · 2026-01-01] — 钩子\n", encoding="utf-8"
    )
    normalize_memory_index(proj)
    assert memory_entrypoint(proj).read_text() == "- [修复 [bug] 的记录](b.md) — 钩子\n"

"""MEMORY.md 索引行的兜底规范化：补全缺失的 ``[type · 日期]``。

主 agent 手写索引行为主；dream 的 prune 阶段调本函数兜底——逐行 parse，对缺
``[type · 日期]`` 的指针行，从对应 topic 文件 frontmatter 取 ``type``、文件 mtime 取写入
日期补上。幂等：已带合法 ``[type · 日期]`` 的行原样保留（dream 更新某条时自行改日期，
这里不覆盖）。非指针行（标题/空行）原样保留。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lumi.agents.memory.paths import memory_dir, memory_entrypoint
from lumi.utils.config.manager import parse_frontmatter

# - [标题](文件.md) 可选 [type · 日期] 可选 — 钩子。标题用非贪婪 `.*?` 锚定到 `](…md)`，
# 故标题内含 `]`（如 `[修复 [bug] 的记录]`）也能正确匹配，不在第一个 `]` 处误断。
_PTR_RE = re.compile(
    r"^(?P<head>\s*-\s*\[.*?\]\((?P<file>[^)]+\.md)\))"
    r"(?:\s*\[(?P<tag>[^\]]*)\])?"
    r"(?P<rest>.*)$"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VALID_TYPES = {"user", "feedback", "project", "reference"}


def _read_type(topic: Path) -> str | None:
    """从 topic 文件 frontmatter 取 type（四类之一），失败返 None。"""
    try:
        content = topic.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _ = parse_frontmatter(content)
    t = meta.get("type")
    return t if t in _VALID_TYPES else None


def _file_date(topic: Path) -> str:
    """topic 文件 mtime 的绝对日期（YYYY-MM-DD，本地时区），作写入日期兜底。

    用本地时区：① 记忆是 home 级本机数据（不跨机器 / 团队共享），字节稳定只需「同一台机器
    build 两次一致」——同机时区固定，本地时区即满足；② 本地日期符合用户直觉，也与主 agent
    手写日期（取 env 的本地 currentDate）同源一致。
    """
    try:
        return datetime.fromtimestamp(topic.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def normalize_memory_index(project_dir: Path) -> None:
    """读 MEMORY.md，对缺 ``[type · 日期]`` 的指针行补全，幂等重写。无索引则跳过。"""
    entry = memory_entrypoint(project_dir)
    mem_dir = memory_dir(project_dir)
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError:
        return

    out_lines: list[str] = []
    changed = False
    for line in text.splitlines():
        m = _PTR_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        tag = (m.group("tag") or "").strip()
        # 已有合法 [type · 日期] → 保留
        if tag and _DATE_RE.search(tag) and any(t in tag for t in _VALID_TYPES):
            out_lines.append(line)
            continue
        topic = mem_dir / m.group("file")
        ttype = _read_type(topic)
        if ttype is None:
            out_lines.append(line)  # 取不到 type，不强补
            continue
        date = _file_date(topic)
        rest = m.group("rest").strip()
        new_line = f"{m.group('head')} [{ttype} · {date}]"
        if rest:
            new_line += f" {rest}"
        out_lines.append(new_line)
        changed = True

    if changed:
        entry.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

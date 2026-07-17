"""MEMORY.md 索引行的兜底规范化：剥掉 legacy ``[tag]``、把写入日期归位到 frontmatter。

主 agent 手写索引行为主；dream 的 prune 阶段调本函数兜底——逐行 parse 指针行：

- **legacy tag**（严格匹配 ``[type · 日期]`` / ``[日期]``，其他方括号内容一概不碰）：
  先把日期回填进 topic 文件 frontmatter，**回填成功才剥 tag**；回填不了（文件缺失 /
  无 frontmatter）则原行保留，信息不丢。此分支是 v0.2.49 前旧索引格式的迁移代码，
  各项目索引都 tag-free 后可连 ``_LEGACY_TAG_RE`` 一起删。
- **无 tag 的新格式行**：topic frontmatter 缺 ``date`` 时以文件 mtime 补一个近似写入
  日期——矛盾裁决按 date 比新旧，近似值好过没有；已有 date 则不动。

幂等：行已纯净且 frontmatter 带 date 时重跑无变化。非指针行（标题/空行）原样保留。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lumi.agents.memory.paths import memory_dir, memory_entrypoint, read_text_or_none
from lumi.utils.config.manager import parse_frontmatter

# - [标题](文件.md) 可选 [tag] 可选 — 结论。标题用非贪婪 `.*?` 锚定到 `](…md)`，
# 故标题内含 `]`（如 `[修复 [bug] 的记录]`）也能正确匹配，不在第一个 `]` 处误断。
_PTR_RE = re.compile(
    r"^(?P<head>\s*-\s*\[.*?\]\((?P<file>[^)]+\.md)\))"
    r"(?:\s*\[(?P<tag>[^\]]*)\])?"
    r"(?P<rest>.*)$"
)
# legacy tag 全量匹配：`type · 日期` 或纯 `日期`；别的方括号内容是正文，不许剥。
_LEGACY_TAG_RE = re.compile(
    r"(?:(?:user|feedback|project|reference)\s*·\s*)?(?P<date>\d{4}-\d{2}-\d{2})"
)


def _file_date(topic: Path) -> str:
    """topic 文件 mtime 的绝对日期（YYYY-MM-DD，本地时区），作缺失 date 的近似值。

    本地时区与主 agent 手写日期（取 env 的本地 currentDate）同源一致。"""
    try:
        return datetime.fromtimestamp(topic.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def _backfill_date(topic: Path, date: str) -> bool:
    """topic frontmatter 缺 ``date`` 时补上；返回 frontmatter 里现在是否有 date。

    边界规则与 :func:`parse_frontmatter` 同一套（lstrip BOM/空白后首行须为 ``---``），
    不会把 date 插到 frontmatter 之外。"""
    content = read_text_or_none(topic)
    if content is None:
        return False
    meta, _ = parse_frontmatter(content)
    if not meta:
        return False
    if "date" in meta:
        return True
    # meta 非空即保证存在独立成行的闭合 ---（与 parse_frontmatter 同套 lstrip 规则）
    lines = content.lstrip("﻿ \t\r\n").split("\n")
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    lines.insert(close, f"date: {date}")
    topic.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def normalize_memory_index(project_dir: Path) -> None:
    """读 MEMORY.md，剥 legacy ``[tag]``（日期先回填 frontmatter）、给缺 date 的
    topic 补近似日期，幂等重写。无索引则跳过。"""
    entry = memory_entrypoint(project_dir)
    mem_dir = memory_dir(project_dir)
    text = read_text_or_none(entry)
    if text is None:
        return

    out_lines: list[str] = []
    for line in text.splitlines():
        m = _PTR_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        topic = mem_dir / m.group("file")
        tag = m.group("tag")
        if tag is None:
            _backfill_date(topic, _file_date(topic))  # 新格式行：只补缺失的 date
            out_lines.append(line)
            continue
        legacy = _LEGACY_TAG_RE.fullmatch(tag.strip())
        if legacy is None or not _backfill_date(topic, legacy.group("date")):
            out_lines.append(line)  # 非 legacy tag / 日期无处安放 → 原样保留
            continue
        rest = m.group("rest").strip()
        out_lines.append(m.group("head") + (f" {rest}" if rest else ""))

    if out_lines != text.splitlines():
        entry.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

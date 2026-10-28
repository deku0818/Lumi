"""为当前格式记忆索引指向的 topic 补齐缺失日期。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lumi.agents.memory.paths import memory_dir, memory_entrypoint, read_text_or_none
from lumi.utils.config.manager import parse_frontmatter

_PTR_RE = re.compile(r"^\s*-\s*\[.*?\]\((?P<file>[^)]+\.md)\)")


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
    """只补 topic 日期，不转换历史索引格式。"""
    text = read_text_or_none(memory_entrypoint(project_dir))
    if text is None:
        return
    mem_dir = memory_dir(project_dir).resolve()
    for line in text.splitlines():
        match = _PTR_RE.match(line)
        if match is None:
            continue
        topic = (mem_dir / match.group("file")).resolve()
        if topic.is_relative_to(mem_dir):
            _backfill_date(topic, _file_date(topic))

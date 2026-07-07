"""记忆索引（MEMORY.md）与项目说明（LUMI.md）的条目/行数据。

两者路径来源不同（``~/.lumi/memory/projects/<sanitize>/MEMORY.md`` vs 项目根
``LUMI.md``），作为两个独立块由 :mod:`context_inject` 组装与 diff。
"""

from __future__ import annotations

import re
from pathlib import Path

from lumi.agents.memory.project_doc import load_project_doc
from lumi.agents.memory.prompt import load_memory_index
from lumi.utils.hashing import short_hash

MEMORY_HEADER = "你的持久记忆索引（MEMORY.md，跨会话保留），仅在与当前任务相关时参考:"
PROJECT_DOC_HEADER = "项目说明（LUMI.md），仅在与当前任务相关时参考:"

# MEMORY.md 索引行 "- [标题](文件名.md) …" 中的文件名——条目的稳定 key
_MEMORY_LINE_KEY = re.compile(r"\(([^()\s]+\.md)\)")


def memory_index_lines(project_dir: Path) -> dict[str, str]:
    """MEMORY.md 索引 → ``{文件名: 行文本}``；解析不出文件名的行以行 hash 为 key。"""
    index = load_memory_index(project_dir)
    if not index:
        return {}
    entries: dict[str, str] = {}
    for line in index.split("\n"):
        match = _MEMORY_LINE_KEY.search(line)
        entries[match.group(1) if match else short_hash(line)] = line
    return entries


def project_doc_lines(project_dir: Path) -> list[str]:
    """项目根 LUMI.md → 行列表（不存在或为空返回 ``[]``）。"""
    doc = load_project_doc(project_dir)
    return doc.split("\n") if doc else []

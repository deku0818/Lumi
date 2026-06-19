"""File-level Checkpoint — diff 统计计算

行级 diff 与变更统计的纯函数（文件数、插入行数、删除行数）。
"""

from __future__ import annotations

import difflib
from pathlib import Path

from lumi.agents.runtime.file_tracker import FileChange
from lumi.utils.logger import logger

_DiffStat = tuple[int, int, int]
"""(files_changed, insertions, deletions)"""


def _line_diff_stat(old: str, new: str) -> tuple[int, int]:
    """计算两段文本之间的行级 diff 统计。

    Returns:
        (insertions, deletions)
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    insertions = 0
    deletions = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag == "replace":
            deletions += i2 - i1
            insertions += j2 - j1
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "insert":
            insertions += j2 - j1
    return insertions, deletions


def _read_text_safe(path: Path) -> str | None:
    """安全读取文本文件，失败返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("[FileCheckpoint] 无法读取文件: %s: %s", path, e)
        return None


def _compute_diff_stat(changes: dict[str, FileChange]) -> _DiffStat:
    """计算变更的 diff 统计（文件数、插入行数、删除行数）。"""
    files = len(changes)
    insertions = 0
    deletions = 0
    for change in changes.values():
        if change.change_type == "created":
            content = _read_text_safe(Path(change.path))
            if content is not None:
                insertions += len(content.splitlines())
        elif change.change_type == "modified" and change.original_content is not None:
            current = _read_text_safe(Path(change.path))
            if current is not None:
                ins, dels = _line_diff_stat(change.original_content, current)
                insertions += ins
                deletions += dels
    return files, insertions, deletions

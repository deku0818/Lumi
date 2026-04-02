"""File Change Tracker — 追踪 edit/write 工具的文件变更

在每轮 agent 执行期间，记录 edit/write 工具修改前的原始文件内容。
用于 checkpoint 系统的文件级回退。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lumi.utils.logger import logger


@dataclass(frozen=True)
class FileChange:
    """单个文件的变更记录"""

    path: str  # 绝对路径
    change_type: str  # "created" | "modified"
    original_content: str | None  # modified 时为原始内容，created 时为 None


class FileChangeTracker:
    """追踪单轮 agent 执行中 edit/write 工具修改的文件。

    使用方式：
    1. 每轮 agent 执行前调用 start_turn()
    2. edit/write 工具在修改文件前调用 record_pre_edit/record_pre_write
    3. 下一轮开始前调用 end_turn() 获取本轮所有变更
    """

    def __init__(self) -> None:
        self._changes: dict[str, FileChange] = {}
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start_turn(self) -> None:
        """清空累积变更，开始追踪新一轮。"""
        self._changes.clear()
        self._active = True

    def end_turn(self) -> dict[str, FileChange]:
        """返回本轮累积的变更并停止追踪。"""
        self._active = False
        result = dict(self._changes)
        self._changes.clear()
        return result

    def peek_changes(self) -> dict[str, FileChange]:
        """返回当前变更的只读快照，不影响追踪状态。"""
        return dict(self._changes)

    def record_pre_write(self, file_path: Path) -> None:
        """write 工具调用前调用：记录文件将被新建。

        仅在文件尚不存在时记录（write 工具对已存在的文件会报错）。
        """
        if not self._active:
            return
        resolved = file_path.resolve()
        key = str(resolved)
        if key in self._changes:
            return
        if resolved.exists():
            # 文件已存在，write 工具会报错，不需要记录
            return
        self._changes[key] = FileChange(
            path=key, change_type="created", original_content=None
        )
        logger.debug("[FileTracker] 记录新建文件: %s", key)

    def record_pre_edit(self, file_path: Path) -> None:
        """edit 工具调用前调用：记录文件修改前的原始内容。

        同一文件在一轮中被多次编辑时，只记录第一次的原始内容。
        """
        if not self._active:
            return
        resolved = file_path.resolve()
        key = str(resolved)
        if key in self._changes:
            return
        if not resolved.exists():
            # 文件不存在，edit 工具会报错，不需要记录
            return
        try:
            original = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("[FileTracker] 无法读取原始内容: %s", key, exc_info=True)
            return
        self._changes[key] = FileChange(
            path=key, change_type="modified", original_content=original
        )
        logger.debug("[FileTracker] 记录修改前内容: %s", key)

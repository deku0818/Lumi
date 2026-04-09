"""File-level Checkpoint — 文件级快照管理

只追踪 edit/write 工具修改的文件，回退时仅恢复这些文件。
不依赖 git，使用目录结构保存原始文件内容。

存储位置：
    ~/.lumi/checkpoints/filediff/{thread_id}/
        meta.json                              # checkpoint 列表
        changes/{checkpoint_id}/
            manifest.json                      # 变更文件清单
            files/{safe_filename}              # 原始文件内容副本
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from lumi.agents.tools.file_tracker import FileChange, FileChangeTracker
from lumi.utils.logger import logger

# ── 常量 ──


_HASH_SHORT_LENGTH = 8
"""checkpoint hash 短格式长度"""

_LOG_LABEL_LENGTH = 50
"""日志中 label 截断长度"""

_LOG_CHECKPOINT_ID_LENGTH = 16
"""日志中 langgraph checkpoint_id 截断长度"""

_DiffStat = tuple[int, int, int]
"""(files_changed, insertions, deletions)"""


@dataclass(frozen=True)
class CheckpointInfo:
    """Checkpoint 摘要信息（用于 UI 展示）"""

    commit_hash: str  # checkpoint hash (full)
    timestamp: float
    label: str  # 用户消息摘要
    langgraph_checkpoint_id: str  # 关联的 LangGraph checkpoint_id
    langgraph_parent_checkpoint_id: str | None = None
    # diff 统计（该轮 agent 执行后产生的文件变更）
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    @property
    def checkpoint_id(self) -> str:
        """checkpoint hash (short)"""
        return self.commit_hash[:_HASH_SHORT_LENGTH]

    @property
    def display_time(self) -> str:
        """格式化相对时间"""
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
        delta = now - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "just now"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return ts.strftime("%m-%d %H:%M")


def _generate_checkpoint_hash() -> str:
    """生成 checkpoint hash"""
    return uuid.uuid4().hex


def _safe_filename(file_path: str) -> str:
    """将绝对路径转为安全的文件名（URL encode）"""
    return urllib.parse.quote(file_path, safe="")


# ── 序列化 ──


def _serialize_checkpoint(info: CheckpointInfo) -> dict[str, Any]:
    """将 CheckpointInfo 序列化为可持久化的 dict。"""
    return {
        "checkpoint_id": info.checkpoint_id,
        "commit_hash": info.commit_hash,
        "timestamp": info.timestamp,
        "label": info.label,
        "langgraph_checkpoint_id": info.langgraph_checkpoint_id,
        "langgraph_parent_checkpoint_id": info.langgraph_parent_checkpoint_id or "",
    }


def _deserialize_checkpoint(d: dict[str, Any]) -> CheckpointInfo:
    """从 dict 反序列化为 CheckpointInfo。

    Raises:
        KeyError: 缺少必要字段
        TypeError: 字段类型不匹配
    """
    parent = d.get("langgraph_parent_checkpoint_id", "") or None
    return CheckpointInfo(
        commit_hash=d["commit_hash"],
        timestamp=d["timestamp"],
        label=d["label"],
        langgraph_checkpoint_id=d["langgraph_checkpoint_id"],
        langgraph_parent_checkpoint_id=parent,
    )


def _serialize_manifest(
    changes: dict[str, FileChange],
) -> list[dict[str, str]]:
    """将文件变更构建为 manifest 条目列表。"""
    manifest: list[dict[str, str]] = []
    for path, change in changes.items():
        manifest.append(
            {
                "path": path,
                "change_type": change.change_type,
                "safe_name": _safe_filename(path),
            }
        )
    return manifest


# ── Diff 计算 ──


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


# ── 原子写入 ──


def _atomic_write_json(path: Path, data: object) -> None:
    """原子写入 JSON 文件（先写临时文件再 rename）。

    使用 tempfile + rename 确保写入不会留下半写状态的文件。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(tmp_fd)
    tmp = Path(tmp_path)
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class FileCheckpointManager:
    """文件级 Checkpoint 管理器

    只追踪 edit/write 工具修改的文件，不影响项目中其他文件。
    """

    def __init__(
        self,
        thread_id: str,
        project_dir: Path,
        tracker: FileChangeTracker,
    ) -> None:
        from lumi.utils.config import GlobalConfigManager

        config = GlobalConfigManager.load()
        base_dir = config.get_checkpoint_dir() / "filediff"
        self._thread_id = thread_id
        self._project_dir = project_dir.resolve()
        self._tracker = tracker
        self._max_checkpoints = config.max_checkpoints
        self._store_dir = (base_dir / thread_id).resolve()
        self._meta_path = self._store_dir / "meta.json"
        self._changes_dir = self._store_dir / "changes"

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    # ── Checkpoint 创建 ──

    def create_checkpoint(
        self,
        label: str,
        langgraph_checkpoint_id: str,
        langgraph_parent_checkpoint_id: str = "",
    ) -> CheckpointInfo | None:
        """在 agent 执行前创建一个 checkpoint。

        1. 收集上一轮 tracker 中的变更，保存到上一个 checkpoint 的 changes 目录
        2. 创建新的 checkpoint 条目
        3. 启动新一轮的变更追踪

        Returns:
            CheckpointInfo，失败返回 None
        """
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            meta = self._persist_previous_turn_changes()
            info = self._append_new_checkpoint(
                meta, label, langgraph_checkpoint_id, langgraph_parent_checkpoint_id
            )
            self._tracker.start_turn()
            return info
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "[FileCheckpoint] 创建 checkpoint 失败: %s", exc, exc_info=True
            )
            return None

    def _persist_previous_turn_changes(self) -> list[dict[str, Any]]:
        """收集上一轮变更并持久化到上一个 checkpoint，返回当前 meta。"""
        prev_changes = self._tracker.end_turn()
        meta = self._load_meta()
        if prev_changes and meta:
            prev_cp_id = meta[-1]["commit_hash"]
            self._save_changes(prev_cp_id, prev_changes)
            logger.info(
                "[FileCheckpoint] 保存上一轮 %d 个文件变更到 %s",
                len(prev_changes),
                prev_cp_id[:_HASH_SHORT_LENGTH],
            )
        return meta

    def _append_new_checkpoint(
        self,
        meta: list[dict[str, Any]],
        label: str,
        langgraph_checkpoint_id: str,
        langgraph_parent_checkpoint_id: str,
    ) -> CheckpointInfo:
        """创建新 checkpoint 条目，追加到 meta 并写盘。"""
        info = CheckpointInfo(
            commit_hash=_generate_checkpoint_hash(),
            timestamp=time.time(),
            label=label,
            langgraph_checkpoint_id=langgraph_checkpoint_id,
            langgraph_parent_checkpoint_id=langgraph_parent_checkpoint_id or None,
        )
        meta.append(_serialize_checkpoint(info))
        self._evict_old_checkpoints(meta)
        self._save_meta(meta)

        logger.info(
            "[FileCheckpoint] checkpoint %s: %s (lg_cp=%s)",
            info.checkpoint_id,
            label[:_LOG_LABEL_LENGTH],
            langgraph_checkpoint_id[:_LOG_CHECKPOINT_ID_LENGTH]
            if langgraph_checkpoint_id
            else "N/A",
        )
        return info

    def _evict_old_checkpoints(self, meta: list[dict[str, Any]]) -> None:
        """淘汰超过上限的旧 checkpoint。"""
        if len(meta) <= self._max_checkpoints:
            return
        removed = meta[: len(meta) - self._max_checkpoints]
        for old in removed:
            self._delete_changes(old["commit_hash"])
        del meta[: len(meta) - self._max_checkpoints]

    # ── Checkpoint 列表 ──

    def list_checkpoints(self) -> list[CheckpointInfo]:
        """列出所有 checkpoint（按时间正序），附带 diff 统计。"""
        try:
            meta = self._load_meta()
            if not meta:
                return []

            infos: list[CheckpointInfo] = []
            last_idx = len(meta) - 1
            for i, d in enumerate(meta):
                try:
                    info = _deserialize_checkpoint(d)
                except (KeyError, TypeError) as e:
                    logger.warning("[FileCheckpoint] 跳过损坏的 meta 条目 %d: %s", i, e)
                    continue

                if i < last_idx:
                    diff = self._diff_stat_from_changes(d["commit_hash"])
                else:
                    diff = self._diff_stat_current()

                infos.append(
                    replace(
                        info,
                        files_changed=diff[0],
                        insertions=diff[1],
                        deletions=diff[2],
                    )
                )
            return infos
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "[FileCheckpoint] 列出 checkpoint 失败: %s", exc, exc_info=True
            )
            return []

    # ── Checkpoint 恢复 ──

    def restore_checkpoint(self, commit_hash: str) -> bool:
        """恢复到指定 checkpoint 的文件状态。

        收集目标 checkpoint 之后所有轮次的文件变更，
        对每个文件取最早的原始内容进行恢复。
        """
        try:
            meta = self._load_meta()
            target_idx = self._find_checkpoint_index(meta, commit_hash)
            if target_idx is None:
                logger.error(
                    "[FileCheckpoint] 未找到 checkpoint: %s",
                    commit_hash[:_HASH_SHORT_LENGTH],
                )
                return False

            restore_map = self._collect_restore_map(meta, target_idx)
            if not self._apply_restore(restore_map):
                return False

            meta = meta[:target_idx]
            self._save_meta(meta)
            self._cleanup_orphan_changes(meta)

            logger.info(
                "[FileCheckpoint] 恢复到 checkpoint %s",
                commit_hash[:_HASH_SHORT_LENGTH],
            )
            return True
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "[FileCheckpoint] 恢复 checkpoint 失败: %s", exc, exc_info=True
            )
            return False

    @staticmethod
    def _find_checkpoint_index(
        meta: list[dict[str, Any]], commit_hash: str
    ) -> int | None:
        """在 meta 列表中查找 commit_hash 对应的索引。"""
        for i, d in enumerate(meta):
            if d["commit_hash"] == commit_hash:
                return i
        return None

    def _collect_restore_map(
        self, meta: list[dict[str, Any]], target_idx: int
    ) -> dict[str, FileChange]:
        """收集目标 checkpoint 及之后所有轮次的文件变更（含当前未持久化的）。"""
        restore_map: dict[str, FileChange] = {}
        for d in meta[target_idx:]:
            for path, change in self._load_changes(d["commit_hash"]).items():
                if path not in restore_map:
                    restore_map[path] = change
        for path, change in self._tracker.end_turn().items():
            if path not in restore_map:
                restore_map[path] = change
        return restore_map

    @staticmethod
    def _apply_restore(restore_map: dict[str, FileChange]) -> bool:
        """按 restore_map 恢复文件，返回是否全部成功。"""
        failed: list[str] = []
        for path, change in restore_map.items():
            try:
                _restore_single_file(path, change)
            except OSError:
                logger.warning("[FileCheckpoint] 恢复文件失败: %s", path, exc_info=True)
                failed.append(path)
        if failed:
            logger.error("[FileCheckpoint] %d 个文件恢复失败: %s", len(failed), failed)
            return False
        return True

    # ── Changes 持久化 ──

    def _save_changes(self, checkpoint_id: str, changes: dict[str, FileChange]) -> None:
        """将一轮的文件变更保存到磁盘。"""
        cp_dir = self._changes_dir / checkpoint_id
        files_dir = cp_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        manifest = _serialize_manifest(changes)
        for entry, (_, change) in zip(manifest, changes.items(), strict=True):
            if change.change_type == "modified" and change.original_content is not None:
                (files_dir / entry["safe_name"]).write_text(
                    change.original_content, encoding="utf-8"
                )
        _atomic_write_json(cp_dir / "manifest.json", manifest)

    def _load_changes(self, checkpoint_id: str) -> dict[str, FileChange]:
        """从磁盘加载一轮的文件变更。"""
        manifest_path = self._changes_dir / checkpoint_id / "manifest.json"
        if not manifest_path.exists():
            return {}

        try:
            manifest: list[dict[str, str]] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "[FileCheckpoint] manifest.json 损坏: %s",
                checkpoint_id[:_HASH_SHORT_LENGTH],
            )
            return {}

        files_dir = self._changes_dir / checkpoint_id / "files"
        changes: dict[str, FileChange] = {}
        for entry in manifest:
            path = entry["path"]
            change_type = entry["change_type"]
            original_content = self._load_original_content(
                entry, files_dir, checkpoint_id
            )
            changes[path] = FileChange(
                path=path,
                change_type=change_type,
                original_content=original_content,
            )
        return changes

    @staticmethod
    def _load_original_content(
        entry: dict[str, str], files_dir: Path, checkpoint_id: str
    ) -> str | None:
        """从备份文件读取原始内容（仅 modified 类型）。"""
        if entry["change_type"] != "modified":
            return None
        safe_name = entry.get("safe_name", _safe_filename(entry["path"]))
        content_path = files_dir / safe_name
        if not content_path.exists():
            logger.warning(
                "[FileCheckpoint] 备份文件缺失，恢复时将跳过: %s (checkpoint=%s)",
                entry["path"],
                checkpoint_id[:_HASH_SHORT_LENGTH],
            )
            return None
        content = _read_text_safe(content_path)
        if content is None:
            logger.warning("[FileCheckpoint] 无法读取原始内容: %s", entry["path"])
        return content

    def _delete_changes(self, checkpoint_id: str) -> None:
        """删除指定 checkpoint 的 changes 目录。"""
        cp_dir = self._changes_dir / checkpoint_id
        if not cp_dir.exists():
            return
        try:
            shutil.rmtree(cp_dir)
        except OSError:
            logger.warning(
                "[FileCheckpoint] 无法清理 changes 目录: %s",
                checkpoint_id[:_HASH_SHORT_LENGTH],
                exc_info=True,
            )

    def _cleanup_orphan_changes(self, meta: list[dict[str, Any]]) -> None:
        """清理不在 meta 中的 changes 目录。"""
        if not self._changes_dir.exists():
            return
        valid_ids = {d["commit_hash"] for d in meta}
        for child in self._changes_dir.iterdir():
            if child.is_dir() and child.name not in valid_ids:
                try:
                    shutil.rmtree(child)
                except OSError:
                    logger.warning(
                        "[FileCheckpoint] 无法清理孤立 changes 目录: %s",
                        child.name[:_HASH_SHORT_LENGTH],
                        exc_info=True,
                    )

    # ── Diff 统计 ──

    def _diff_stat_from_changes(self, checkpoint_id: str) -> _DiffStat:
        """从已保存的 changes 目录计算 diff 统计。"""
        changes = self._load_changes(checkpoint_id)
        if not changes:
            return 0, 0, 0
        return _compute_diff_stat(changes)

    def _diff_stat_current(self) -> _DiffStat:
        """从当前 tracker 内存中的变更计算 diff 统计（只读）。"""
        changes = self._tracker.peek_changes()
        if not changes:
            return 0, 0, 0
        return _compute_diff_stat(changes)

    # ── Meta 文件管理 ──

    def _load_meta(self) -> list[dict[str, Any]]:
        """加载 meta.json，损坏时备份并重置。"""
        if not self._meta_path.exists():
            return []
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            logger.error("[FileCheckpoint] meta.json 损坏，已备份后重置", exc_info=True)
            self._backup_corrupted_meta()
            return []

    def _backup_corrupted_meta(self) -> None:
        """备份或删除损坏的 meta.json。"""
        try:
            backup = self._meta_path.with_suffix(".json.bak")
            self._meta_path.rename(backup)
        except OSError:
            logger.error(
                "[FileCheckpoint] 无法备份损坏的 meta.json，尝试删除",
                exc_info=True,
            )
            try:
                self._meta_path.unlink()
            except OSError:
                logger.error("[FileCheckpoint] 无法删除损坏的 meta.json", exc_info=True)

    def _save_meta(self, meta: list[dict[str, Any]]) -> None:
        """原子写入 meta.json"""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._meta_path, meta)


# ── 文件恢复 ──


def _restore_single_file(path: str, change: FileChange) -> None:
    """恢复单个文件到原始状态。

    Raises:
        OSError: 文件操作失败
    """
    p = Path(path)
    if change.change_type == "created":
        if p.exists() and p.is_file():
            p.unlink()
            logger.debug("[FileCheckpoint] 删除文件: %s", path)
    elif change.change_type == "modified" and change.original_content is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(change.original_content, encoding="utf-8")
        logger.debug("[FileCheckpoint] 恢复文件: %s", path)


# ── 过期 thread 清理 ──


def cleanup_stale_threads() -> int:
    """清理超过配置天数未更新的 thread 目录。

    从 GlobalConfig 读取 checkpoint_dir 和 stale_thread_days，
    扫描 filediff 目录下所有 thread 子目录，根据 meta.json 中最新
    checkpoint 的 timestamp 判断是否过期。

    Returns:
        删除的 thread 目录数量
    """
    from lumi.utils.config import GlobalConfigManager

    config = GlobalConfigManager.load()
    stale_days = config.stale_thread_days
    if stale_days <= 0:
        return 0
    base_dir = config.get_checkpoint_dir() / "filediff"
    if not base_dir.is_dir():
        return 0

    cutoff = time.time() - stale_days * 86400
    removed = 0

    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"

        # 无 meta.json → 孤立目录，清理
        if not meta_path.exists():
            if _remove_thread_dir(child):
                removed += 1
            continue

        # 读取 meta.json
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            logger.warning("[FileCheckpoint] 跳过损坏的 thread 目录: %s", child.name)
            continue

        # 空列表 → 无 checkpoint，清理
        if not meta:
            if _remove_thread_dir(child):
                removed += 1
            continue

        # 取最新 timestamp
        max_ts = max(
            (d.get("timestamp", 0) for d in meta if isinstance(d, dict)),
            default=0,
        )
        if max_ts < cutoff:
            if _remove_thread_dir(child):
                removed += 1

    return removed


def _remove_thread_dir(thread_dir: Path) -> bool:
    """安全删除 thread 目录，失败时仅 warning。"""
    try:
        shutil.rmtree(thread_dir)
        logger.info("[FileCheckpoint] 清理过期 thread 目录: %s", thread_dir.name)
        return True
    except OSError:
        logger.warning(
            "[FileCheckpoint] 无法清理 thread 目录: %s",
            thread_dir.name,
            exc_info=True,
        )
        return False

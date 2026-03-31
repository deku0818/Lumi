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
import shutil
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from lumi.agents.tools.file_tracker import FileChange, FileChangeTracker
from lumi.utils.logger import logger

# 单个 thread 最多保留的 checkpoint 数量
_MAX_CHECKPOINTS = 20


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
        """checkpoint hash (short, 前 8 位)"""
        return self.commit_hash[:8]

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


def _generate_checkpoint_hash(thread_id: str, timestamp: float) -> str:
    """生成 checkpoint hash"""
    return uuid.uuid4().hex


def _safe_filename(file_path: str) -> str:
    """将绝对路径转为安全的文件名（URL encode）"""
    return urllib.parse.quote(file_path, safe="")


class FileCheckpointManager:
    """文件级 Checkpoint 管理器

    只追踪 edit/write 工具修改的文件，不影响项目中其他文件。
    """

    def __init__(
        self,
        thread_id: str,
        project_dir: Path,
        tracker: FileChangeTracker,
        base_dir: Path | None = None,
    ):
        """
        Args:
            thread_id: 会话线程 ID
            project_dir: 项目根目录路径
            tracker: 文件变更追踪器
            base_dir: 存储根目录，默认 ~/.lumi/checkpoints/filediff
        """
        if base_dir is None:
            base_dir = Path.home() / ".lumi" / "checkpoints" / "filediff"
        self._thread_id = thread_id
        self._project_dir = project_dir.resolve()
        self._tracker = tracker
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

        Args:
            label: 用户消息摘要
            langgraph_checkpoint_id: 当前 LangGraph checkpoint_id
            langgraph_parent_checkpoint_id: LangGraph parent checkpoint_id

        Returns:
            CheckpointInfo，失败返回 None
        """
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)

            # 1. 收集上一轮变更并持久化到上一个 checkpoint
            prev_changes = self._tracker.end_turn()
            meta = self._load_meta()
            if prev_changes and meta:
                prev_cp_id = meta[-1]["commit_hash"]
                self._save_changes(prev_cp_id, prev_changes)
                logger.info(
                    "[FileCheckpoint] 保存上一轮 %d 个文件变更到 %s",
                    len(prev_changes),
                    prev_cp_id[:8],
                )

            # 2. 创建新 checkpoint
            now = time.time()
            commit_hash = _generate_checkpoint_hash(self._thread_id, now)

            info = CheckpointInfo(
                commit_hash=commit_hash,
                timestamp=now,
                label=label[:100],
                langgraph_checkpoint_id=langgraph_checkpoint_id,
                langgraph_parent_checkpoint_id=langgraph_parent_checkpoint_id or None,
            )

            # 追加到 meta（复用已加载的 meta）
            meta.append(self._info_to_dict(info))
            if len(meta) > _MAX_CHECKPOINTS:
                # 清理最旧的 checkpoint 的 changes 目录
                removed = meta[: len(meta) - _MAX_CHECKPOINTS]
                for old in removed:
                    self._delete_changes(old["commit_hash"])
                meta = meta[-_MAX_CHECKPOINTS:]
            self._save_meta(meta)

            # 3. 开始追踪新一轮
            self._tracker.start_turn()

            logger.info(
                "[FileCheckpoint] checkpoint %s: %s (lg_cp=%s)",
                info.checkpoint_id,
                label[:50],
                langgraph_checkpoint_id[:16] if langgraph_checkpoint_id else "N/A",
            )
            return info

        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "[FileCheckpoint] 创建 checkpoint 失败: %s", exc, exc_info=True
            )
            return None

    # ── Checkpoint 列表 ──

    def list_checkpoints(self) -> list[CheckpointInfo]:
        """列出所有 checkpoint（按时间正序），附带 diff 统计。

        diff 归属逻辑：每个 checkpoint 显示的是该轮 agent 执行后产生的文件变更。
        """
        try:
            meta = self._load_meta()
            if not meta:
                return []

            infos: list[CheckpointInfo] = []
            for i, d in enumerate(meta):
                try:
                    info = self._dict_to_info(d)
                except (KeyError, TypeError) as e:
                    logger.warning("[FileCheckpoint] 跳过损坏的 meta 条目 %d: %s", i, e)
                    continue

                # 计算 diff 统计
                cp_id = d["commit_hash"]
                if i + 1 < len(meta):
                    # 非最后一个：从已保存的 changes 目录读取
                    files, ins, dels = self._diff_stat_from_changes(cp_id)
                else:
                    # 最后一个：从 tracker 内存 + 当前文件对比
                    files, ins, dels = self._diff_stat_current()

                info = CheckpointInfo(
                    commit_hash=info.commit_hash,
                    timestamp=info.timestamp,
                    label=info.label,
                    langgraph_checkpoint_id=info.langgraph_checkpoint_id,
                    langgraph_parent_checkpoint_id=info.langgraph_parent_checkpoint_id,
                    files_changed=files,
                    insertions=ins,
                    deletions=dels,
                )
                infos.append(info)
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

        Args:
            commit_hash: 要恢复到的 checkpoint hash

        Returns:
            恢复成功返回 True
        """
        try:
            meta = self._load_meta()
            target_idx = None
            for i, d in enumerate(meta):
                if d["commit_hash"] == commit_hash:
                    target_idx = i
                    break

            if target_idx is None:
                logger.error("[FileCheckpoint] 未找到 checkpoint: %s", commit_hash[:8])
                return False

            # 收集目标及之后所有 checkpoint 的变更
            # 目标 checkpoint 自身的 changes 也需要恢复（它代表该轮的文件变更），
            # 因为回滚到目标意味着回到"该轮执行前"的状态。
            # 同时收集当前 tracker 中尚未持久化的变更。
            restore_map: dict[str, FileChange] = {}

            for d in meta[target_idx:]:
                changes = self._load_changes(d["commit_hash"])
                for path, change in changes.items():
                    if path not in restore_map:
                        restore_map[path] = change

            # 加入当前 tracker 中的变更（最新一轮，尚未持久化）
            current_changes = self._tracker.end_turn()
            for path, change in current_changes.items():
                if path not in restore_map:
                    restore_map[path] = change

            # 执行恢复
            failed: list[str] = []
            for path, change in restore_map.items():
                try:
                    p = Path(path)
                    if change.change_type == "created":
                        # 新建的文件：删除
                        if p.exists() and p.is_file():
                            p.unlink()
                            logger.debug("[FileCheckpoint] 删除文件: %s", path)
                    elif (
                        change.change_type == "modified"
                        and change.original_content is not None
                    ):
                        # 修改的文件：写回原始内容
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(change.original_content, encoding="utf-8")
                        logger.debug("[FileCheckpoint] 恢复文件: %s", path)
                except Exception:
                    logger.warning(
                        "[FileCheckpoint] 恢复文件失败: %s", path, exc_info=True
                    )
                    failed.append(path)

            if failed:
                logger.error(
                    "[FileCheckpoint] %d 个文件恢复失败: %s", len(failed), failed
                )
                return False

            # 截断 meta 到目标之前（排除目标本身，用户回滚后会重新发送创建新 checkpoint）
            meta = meta[:target_idx]
            self._save_meta(meta)

            # 清理多余的 changes 目录
            self._cleanup_orphan_changes(meta)

            logger.info("[FileCheckpoint] 恢复到 checkpoint %s", commit_hash[:8])
            return True

        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error(
                "[FileCheckpoint] 恢复 checkpoint 失败: %s", exc, exc_info=True
            )
            return False

    # ── Changes 持久化 ──

    def _save_changes(self, checkpoint_id: str, changes: dict[str, FileChange]) -> None:
        """将一轮的文件变更保存到磁盘。"""
        cp_dir = self._changes_dir / checkpoint_id
        files_dir = cp_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        manifest = []
        for path, change in changes.items():
            safe_name = _safe_filename(path)
            manifest.append(
                {
                    "path": path,
                    "change_type": change.change_type,
                    "safe_name": safe_name,
                }
            )
            if change.change_type == "modified" and change.original_content is not None:
                (files_dir / safe_name).write_text(
                    change.original_content, encoding="utf-8"
                )

        self._atomic_write_json(cp_dir / "manifest.json", manifest)

    def _load_changes(self, checkpoint_id: str) -> dict[str, FileChange]:
        """从磁盘加载一轮的文件变更。"""
        cp_dir = self._changes_dir / checkpoint_id
        manifest_path = cp_dir / "manifest.json"
        if not manifest_path.exists():
            return {}

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("[FileCheckpoint] manifest.json 损坏: %s", checkpoint_id[:8])
            return {}

        changes: dict[str, FileChange] = {}
        files_dir = cp_dir / "files"
        for entry in manifest:
            path = entry["path"]
            change_type = entry["change_type"]
            original_content = None
            if change_type == "modified":
                safe_name = entry.get("safe_name", _safe_filename(path))
                content_path = files_dir / safe_name
                if content_path.exists():
                    try:
                        original_content = content_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        logger.warning("[FileCheckpoint] 无法读取原始内容: %s", path)
                else:
                    logger.warning(
                        "[FileCheckpoint] 备份文件缺失，恢复时将跳过: %s (checkpoint=%s)",
                        path,
                        checkpoint_id[:8],
                    )
            changes[path] = FileChange(
                path=path,
                change_type=change_type,
                original_content=original_content,
            )
        return changes

    def _delete_changes(self, checkpoint_id: str) -> None:
        """删除指定 checkpoint 的 changes 目录。"""
        cp_dir = self._changes_dir / checkpoint_id
        if cp_dir.exists():
            try:
                shutil.rmtree(cp_dir)
            except OSError:
                logger.warning(
                    "[FileCheckpoint] 无法清理 changes 目录: %s",
                    checkpoint_id[:8],
                    exc_info=True,
                )

    def _cleanup_orphan_changes(self, meta: list[dict]) -> None:
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
                        child.name[:8],
                        exc_info=True,
                    )

    # ── Diff 统计 ──

    def _diff_stat_from_changes(self, checkpoint_id: str) -> tuple[int, int, int]:
        """从已保存的 changes 目录计算 diff 统计。"""
        changes = self._load_changes(checkpoint_id)
        if not changes:
            return 0, 0, 0
        return self._compute_diff_stat(changes)

    def _diff_stat_current(self) -> tuple[int, int, int]:
        """从当前 tracker 内存中的变更计算 diff 统计。

        不消耗 tracker 状态（只读取，不 end_turn）。
        """
        changes = self._tracker.peek_changes()
        if not changes:
            return 0, 0, 0
        return self._compute_diff_stat(changes)

    @staticmethod
    def _compute_diff_stat(changes: dict[str, FileChange]) -> tuple[int, int, int]:
        """计算变更的 diff 统计（文件数、插入行数、删除行数）。

        对比原始内容与变更后内容（从下一个 checkpoint 的原始内容推断，
        但由于我们可能没有下一个 checkpoint 的数据，简化为统计 manifest 条目数
        和原始内容的行数变化）。
        """
        files = len(changes)
        insertions = 0
        deletions = 0
        for change in changes.values():
            if change.change_type == "created":
                # 新建文件：当前文件内容全部算作插入
                p = Path(change.path)
                if p.exists():
                    try:
                        content = p.read_text(encoding="utf-8")
                        insertions += len(content.splitlines())
                    except (OSError, UnicodeDecodeError):
                        logger.debug(
                            "[FileCheckpoint] 无法读取文件计算 diff: %s",
                            change.path,
                        )
            elif (
                change.change_type == "modified" and change.original_content is not None
            ):
                # 修改文件：对比原始内容和当前内容
                p = Path(change.path)
                if p.exists():
                    try:
                        current = p.read_text(encoding="utf-8")
                        ins, dels = _line_diff_stat(change.original_content, current)
                        insertions += ins
                        deletions += dels
                    except (OSError, UnicodeDecodeError):
                        logger.debug(
                            "[FileCheckpoint] 无法读取文件计算 diff: %s",
                            change.path,
                        )
        return files, insertions, deletions

    # ── Meta 文件管理 ──

    def _load_meta(self) -> list[dict]:
        if not self._meta_path.exists():
            return []
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            logger.error("[FileCheckpoint] meta.json 损坏，已备份后重置", exc_info=True)
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
                    logger.error(
                        "[FileCheckpoint] 无法删除损坏的 meta.json", exc_info=True
                    )
            return []

    def _save_meta(self, meta: list[dict]) -> None:
        """原子写入 meta.json"""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._meta_path, meta)

    def _atomic_write_json(self, path: Path, data: object) -> None:
        """原子写入 JSON 文件（先写临时文件再 rename）"""
        import os

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

    @staticmethod
    def _info_to_dict(info: CheckpointInfo) -> dict:
        return {
            "checkpoint_id": info.checkpoint_id,
            "commit_hash": info.commit_hash,
            "timestamp": info.timestamp,
            "label": info.label,
            "langgraph_checkpoint_id": info.langgraph_checkpoint_id,
            "langgraph_parent_checkpoint_id": info.langgraph_parent_checkpoint_id or "",
        }

    @staticmethod
    def _dict_to_info(d: dict) -> CheckpointInfo:
        parent = d.get("langgraph_parent_checkpoint_id", "") or None
        return CheckpointInfo(
            commit_hash=d["commit_hash"],
            timestamp=d["timestamp"],
            label=d["label"],
            langgraph_checkpoint_id=d["langgraph_checkpoint_id"],
            langgraph_parent_checkpoint_id=parent,
        )


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

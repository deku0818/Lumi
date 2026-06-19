"""File-level Checkpoint — 序列化与摘要信息

CheckpointInfo 数据结构及其 meta.json / manifest.json 序列化逻辑。
不改任何 JSON 字段名/格式（与已存在的 checkpoint 文件兼容）。
"""

from __future__ import annotations

import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from lumi.agents.runtime.file_tracker import FileChange

# ── 常量 ──


_HASH_SHORT_LENGTH = 8
"""checkpoint hash 短格式长度"""


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
        from datetime import datetime

        now = datetime.now(tz=UTC)
        ts = datetime.fromtimestamp(self.timestamp, tz=UTC)
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

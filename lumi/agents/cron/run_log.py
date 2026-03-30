"""RunLog：执行日志，JSONL 格式存储。

每个任务的执行记录存储在独立的 JSONL 文件中（`.lumi/cron/runs/{job_id}.jsonl`），
每行一条 JSON 记录。超过 2MB 时自动裁剪旧记录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger("Lumi")

# 单个 JSONL 文件的最大大小（字节）
_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB


@dataclass(frozen=True)
class RunRecord:
    """单次任务执行记录。

    Attributes:
        job_id: 任务 ID。
        job_name: 任务名称。
        started_at: 执行开始时间。
        finished_at: 执行结束时间。
        status: 执行状态（success/failed/timeout）。
        duration_ms: 执行耗时（毫秒）。
        output_summary: 输出摘要，截取前 500 字符。
        error: 错误信息，成功时为空字符串。
    """

    job_id: str
    job_name: str
    started_at: datetime
    finished_at: datetime
    status: Literal["success", "failed", "timeout"]
    duration_ms: int
    output_summary: str
    error: str = ""

    def to_dict(self) -> dict:
        """序列化为字典，用于 JSON 存储。"""
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "output_summary": self.output_summary,
            "error": self.error,
        }

    @staticmethod
    def from_dict(data: dict) -> RunRecord:
        """从字典反序列化为 RunRecord。

        Args:
            data: 包含记录字段的字典，通常来自 JSONL 行。

        Returns:
            反序列化后的 RunRecord 实例。
        """
        return RunRecord(
            job_id=data["job_id"],
            job_name=data["job_name"],
            started_at=datetime.fromisoformat(data["started_at"]),
            finished_at=datetime.fromisoformat(data["finished_at"]),
            status=data["status"],
            duration_ms=data["duration_ms"],
            output_summary=data["output_summary"],
            error=data.get("error", ""),
        )


def _log_path(base_dir: Path, job_id: str) -> Path:
    """获取指定任务的 JSONL 日志文件路径。"""
    return base_dir / f"{job_id}.jsonl"


def _append_record_sync(path: Path, line: str) -> None:
    """同步追加一行到 JSONL 文件，供 asyncio.to_thread 调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _trim_file_sync(path: Path) -> None:
    """同步裁剪超过 2MB 的 JSONL 文件，保留后半部分记录。

    策略：读取所有行，保留后半部分，原子写回。
    """
    if not path.exists():
        return
    size = path.stat().st_size
    if size <= _MAX_FILE_SIZE:
        return

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # 保留后半部分行
    keep = len(lines) // 2
    kept_lines = lines[-keep:] if keep > 0 else lines[-1:]

    # 原子写入：先写临时文件，再 rename
    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(kept_lines)
        os.replace(tmp_path_str, path)
    except BaseException:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _read_lines_sync(path: Path) -> list[str]:
    """同步读取 JSONL 文件所有行，供 asyncio.to_thread 调用。"""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return f.readlines()


class RunLog:
    """执行日志管理，基于 JSONL 文件。

    每个任务的执行记录存储在 `{base_dir}/{job_id}.jsonl` 中，
    每行一条 JSON 记录。超过 2MB 时自动裁剪旧记录。

    Args:
        base_dir: 日志存储目录，如 `.lumi/cron/runs/`。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    async def append(self, record: RunRecord) -> None:
        """追加一条执行记录到对应任务的 JSONL 文件。

        写入后检查文件大小，超过 2MB 自动裁剪旧记录。

        Args:
            record: 要追加的执行记录。
        """
        path = _log_path(self._base_dir, record.job_id)
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        await asyncio.to_thread(_append_record_sync, path, line)

        # 检查并裁剪
        try:
            await asyncio.to_thread(_trim_file_sync, path)
        except OSError:
            logger.warning("裁剪日志文件失败: %s", path, exc_info=True)

    def get_last_run_sync(self, job_id: str) -> RunRecord | None:
        """同步获取指定任务最近一条执行记录。

        用于启动时检查错过的任务，避免在 async context 之外使用。

        Args:
            job_id: 任务 ID。

        Returns:
            最近的执行记录，无记录时返回 None。
        """
        path = _log_path(self._base_dir, job_id)
        lines = _read_lines_sync(path)
        if not lines:
            return None

        # 从后往前找第一条可解析的记录（最新的）
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                return RunRecord.from_dict(data)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning(
                    "跳过无法解析的日志行: %s (job_id=%s)", line[:100], job_id
                )
                continue
        return None

    async def get_recent(self, job_id: str, limit: int = 20) -> list[RunRecord]:
        """获取指定任务最近 N 条执行记录，按时间倒序。

        Args:
            job_id: 任务 ID。
            limit: 返回的最大记录数，默认 20。

        Returns:
            最近的执行记录列表，按 started_at 倒序排列。
        """
        path = _log_path(self._base_dir, job_id)
        lines = await asyncio.to_thread(_read_lines_sync, path)

        records: list[RunRecord] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(RunRecord.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("跳过无法解析的日志行: %s", line[:100])
                continue

        # 按 started_at 倒序排列，取最近 limit 条
        records.sort(key=lambda r: r.started_at, reverse=True)
        return records[:limit]

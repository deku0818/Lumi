"""RunLog：执行日志，JSONL 格式存储。

每个任务的执行记录存储在独立的 JSONL 文件中（`~/.lumi/cron/<workspace>/runs/{job_id}.jsonl`），
每行一条 JSON 记录。超过 2MB 时自动裁剪旧记录。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from lumi.utils.atomic_io import atomic_write_text
from lumi.utils.constants import MAX_RUN_LOG_FILE_SIZE
from lumi.utils.logger import logger


@dataclass(frozen=True)
class RunRecord:
    """单次任务执行记录。

    Attributes:
        job_id: 任务 ID。
        job_name: 任务名称。
        started_at: 执行开始时间。
        finished_at: 执行结束时间。
        status: 执行状态（success/failed/timeout/stopped）。
        duration_ms: 执行耗时（毫秒）。
        output_summary: 输出摘要，截取前 500 字符。
        error: 错误信息，成功时为空字符串。
        thread_id: 本次执行的会话线程 ID（cron- 前缀），可在前端跳转续聊；
                   空串表示无会话（checkpoint 未启用或已被保留策略清理）。
    """

    job_id: str
    job_name: str
    started_at: datetime
    finished_at: datetime
    status: Literal["success", "failed", "timeout", "stopped"]
    duration_ms: int
    output_summary: str
    error: str = ""
    thread_id: str = ""

    def to_dict(self) -> dict[str, object]:
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
            "thread_id": self.thread_id,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> RunRecord:
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
            thread_id=data.get("thread_id", ""),
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
    if size <= MAX_RUN_LOG_FILE_SIZE:
        return

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # 保留后半部分行
    keep = len(lines) // 2
    kept_lines = lines[-keep:] if keep > 0 else lines[-1:]

    atomic_write_text(path, "".join(kept_lines))


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
        base_dir: 日志存储目录，如 `~/.lumi/cron/<workspace>/runs/`。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        # append 与 prune 的读-改-写互斥：同一任务的执行可能重叠
        # （Run now 撞上定时触发），prune 的全文件重写会吞掉并发 append 的记录
        self._write_lock = asyncio.Lock()

    async def append(self, record: RunRecord) -> None:
        """追加一条执行记录到对应任务的 JSONL 文件。

        写入后检查文件大小，超过 2MB 自动裁剪旧记录。

        Args:
            record: 要追加的执行记录。
        """
        path = _log_path(self._base_dir, record.job_id)
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        async with self._write_lock:
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

    async def get_all(self, job_id: str) -> list[RunRecord]:
        """获取指定任务全部执行记录，按 started_at 倒序。

        Args:
            job_id: 任务 ID。

        Returns:
            全部执行记录列表（文件有 2MB 裁剪上限，读取有界）。
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

        records.sort(key=lambda r: r.started_at, reverse=True)
        return records

    async def get_recent(self, job_id: str, limit: int = 20) -> list[RunRecord]:
        """获取指定任务最近 N 条执行记录，按时间倒序。

        Args:
            job_id: 任务 ID。
            limit: 返回的最大记录数，默认 20。

        Returns:
            最近的执行记录列表，按 started_at 倒序排列。
        """
        return (await self.get_all(job_id))[:limit]

    async def recent_thread_ids(self, job_id: str, keep: int) -> list[str]:
        """最近 keep 条记录里非空的 thread_id，从新到旧。

        与 get_recent 的区别只在开销：同样读整个文件，但只反序列化尾部 keep 行，
        不构造 RunRecord 也不排序。本方法在 list_cron_jobs 热路径上（每条 cron.result
        都会触发一次列表刷新），而日志文件上限有 2MB、可达数千条记录。

        代价是窗口按文件物理顺序取（追加序 = 完成序），并发执行重叠时与 get_recent
        的 started_at 排序可能差一两条——对「有哪些 run 可跳转」这个用途无影响。

        Args:
            job_id: 任务 ID。
            keep: 回看的最近记录条数。

        Returns:
            可跳转会话的 thread_id 列表（从新到旧）。
        """
        path = _log_path(self._base_dir, job_id)
        lines = await asyncio.to_thread(_read_lines_sync, path)

        tids: list[str] = []
        for line in reversed(lines[-keep:]):
            line = line.strip()
            if not line:
                continue
            try:
                tid = json.loads(line).get("thread_id") or ""
            except (json.JSONDecodeError, AttributeError):
                logger.warning(
                    "跳过无法解析的日志行: %s (job_id=%s)", line[:100], job_id
                )
                continue
            if tid:
                tids.append(tid)
        return tids

    async def prune_thread_ids(self, job_id: str, keep: int) -> list[str]:
        """会话保留策略：只保留最近 keep 条记录的 thread_id，返回被清理的部分。

        记录本身保留（执行历史仍可见），仅清空超出部分的 thread_id 并原子写回，
        调用方负责删除对应的 checkpoint 线程。重复调用幂等（已清空的不再返回）。

        Args:
            job_id: 任务 ID。
            keep: 保留会话的最近记录条数。

        Returns:
            被清理的 thread_id 列表（按时间从新到旧）。
        """
        async with self._write_lock:
            records = await self.get_all(job_id)
            pruned = [r.thread_id for r in records[keep:] if r.thread_id]
            if not pruned:
                return []

            kept: list[RunRecord] = records[:keep] + [
                replace(r, thread_id="") for r in records[keep:]
            ]
            # get_recent 是倒序，写回按时间正序（与追加写入的自然顺序一致）
            content = "".join(
                json.dumps(r.to_dict(), ensure_ascii=False) + "\n"
                for r in reversed(kept)
            )
            path = _log_path(self._base_dir, job_id)
            await asyncio.to_thread(atomic_write_text, path, content)
            return pruned

    async def delete_log(self, job_id: str) -> None:
        """删除指定任务的整个日志文件（任务级联删除用）。"""
        path = _log_path(self._base_dir, job_id)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            pass

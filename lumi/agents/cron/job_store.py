"""JobStore：任务持久化，JSON 文件存储。

通过原子写入（write-to-temp + rename）确保数据安全，
文件损坏时自动备份为 .bak 并返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from lumi.agents.cron.models import Job

logger = logging.getLogger("Lumi")

# 持久化格式版本号
_FORMAT_VERSION = 1


def _read_file(path: Path) -> str:
    """同步读取文件内容，供 asyncio.to_thread 调用。"""
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, content: str) -> None:
    """同步原子写入：先写临时文件，再 rename，供 asyncio.to_thread 调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path_str, path)
    except BaseException:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _backup_corrupt_file(path: Path) -> None:
    """同步备份损坏文件为 .bak，供 asyncio.to_thread 调用。"""
    bak_path = path.with_suffix(".bak")
    os.replace(path, bak_path)


class JobStore:
    """任务持久化存储，基于 JSON 文件。

    数据格式：
    ```json
    {
        "version": 1,
        "jobs": [...]
    }
    ```

    Args:
        path: 持久化文件路径，如 `.lumi/cron/jobs.json`。
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> list[Job]:
        """从磁盘加载任务列表。

        - 文件不存在或为空：返回空列表
        - 文件损坏无法解析：备份为 `.bak`，记录错误日志，返回空列表

        Returns:
            加载到的任务列表。
        """
        if not self._path.exists():
            return []

        try:
            content = await asyncio.to_thread(_read_file, self._path)
        except OSError:
            logger.error("读取任务文件失败: %s", self._path, exc_info=True)
            return []

        if not content.strip():
            return []

        try:
            data = json.loads(content)
            return [Job.from_dict(j) for j in data.get("jobs", [])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.error(
                "任务文件损坏，备份为 .bak 并使用空列表启动: %s",
                self._path,
                exc_info=True,
            )
            try:
                await asyncio.to_thread(_backup_corrupt_file, self._path)
            except OSError:
                logger.error("备份损坏文件失败: %s", self._path, exc_info=True)
            return []

    async def save(self, jobs: list[Job]) -> None:
        """原子写入任务列表到磁盘。

        先写入临时文件，再通过 os.replace 替换目标文件，
        确保写入过程中断电或崩溃不会损坏数据。

        Args:
            jobs: 要保存的任务列表。
        """
        data = {
            "version": _FORMAT_VERSION,
            "jobs": [job.to_dict() for job in jobs],
        }
        content = json.dumps(data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_atomic_write, self._path, content)

    async def upsert(self, job: Job) -> None:
        """创建或更新单个任务。

        如果已存在相同 ID 的任务则替换，否则追加。

        Args:
            job: 要创建或更新的任务。
        """
        jobs = await self.load()
        for i, existing in enumerate(jobs):
            if existing.id == job.id:
                jobs[i] = job
                break
        else:
            jobs.append(job)
        await self.save(jobs)

    async def delete(self, job_id: str) -> bool:
        """删除指定 ID 的任务。

        Args:
            job_id: 要删除的任务 ID。

        Returns:
            是否成功删除（ID 存在则为 True）。
        """
        jobs = await self.load()
        new_jobs = [j for j in jobs if j.id != job_id]
        if len(new_jobs) == len(jobs):
            return False
        await self.save(new_jobs)
        return True

    async def get_all(self) -> list[Job]:
        """获取所有任务。

        Returns:
            当前存储的所有任务列表。
        """
        return await self.load()

    async def get(self, job_id: str) -> Job | None:
        """按 ID 获取单个任务。

        Args:
            job_id: 任务 ID。

        Returns:
            匹配的任务，不存在则返回 None。
        """
        jobs = await self.load()
        for job in jobs:
            if job.id == job_id:
                return job
        return None

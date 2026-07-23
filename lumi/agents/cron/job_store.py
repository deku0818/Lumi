"""JobStore：任务持久化，JSON 文件存储。

通过原子写入（write-to-temp + rename）确保数据安全，
文件损坏时自动备份为 .bak 并返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path

from lumi.agents.cron.models import Job
from lumi.utils.atomic_io import atomic_write_text
from lumi.utils.logger import logger

# 持久化格式版本号
_FORMAT_VERSION = 1


def _read_file(path: Path) -> str:
    """同步读取文件内容，供 asyncio.to_thread 调用。"""
    return path.read_text(encoding="utf-8")


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
        self._lock = asyncio.Lock()
        # 任务增删改变更观察者：gateway 层注册，把变更广播给 desktop 刷新任务列表
        # （同 bg_tasks 的 TaskRegistry.set_on_change 模式）。tool 与 UI 两条路都经
        # 本 store 落盘，故这里是唯一 choke point。TUI / 测试不设 → 不广播。
        self._on_change: Callable[[], None] | None = None

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        """注册任务变更观察者（upsert / delete 落盘后触发）。"""
        self._on_change = callback

    def _fire_change(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            logger.error("[JobStore] on_change 回调异常", exc_info=True)

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
        await asyncio.to_thread(atomic_write_text, self._path, content)

    async def upsert(self, job: Job, *, notify: bool = True) -> None:
        """创建或更新单个任务。

        如果已存在相同 ID 的任务则替换，否则追加。
        使用锁防止并发写入导致数据丢失。

        Args:
            job: 要创建或更新的任务。
            notify: 是否触发变更观察者。用户增删改用默认 True；内部记账（如
                consecutive_errors 退避计数持久化）传 False，避免非用户可见的
                字段变更也触发前端全量刷新。
        """
        async with self._lock:
            jobs = await self.load()
            for i, existing in enumerate(jobs):
                if existing.id == job.id:
                    jobs[i] = job
                    break
            else:
                jobs.append(job)
            await self.save(jobs)
        if notify:
            self._fire_change()

    async def delete(self, job_id: str) -> bool:
        """删除指定 ID 的任务。

        使用锁防止并发写入导致数据丢失。

        Args:
            job_id: 要删除的任务 ID。

        Returns:
            是否成功删除（ID 存在则为 True）。
        """
        async with self._lock:
            jobs = await self.load()
            new_jobs = [j for j in jobs if j.id != job_id]
            if len(new_jobs) == len(jobs):
                return False
            await self.save(new_jobs)
        self._fire_change()
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

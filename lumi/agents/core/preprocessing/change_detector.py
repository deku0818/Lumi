"""文件集变更检测器基类

基于每个文件的 (key, mtime_ns, size) 算 SHA-256 digest 判断目录是否变更，
避免每轮都重新解析文件内容。子类只需提供「扫哪些文件 / 用什么 key / 如何加载」；
本类负责 digest 比对、缓存与单例管理。

``_INITIAL_DIGEST`` 控制首次 ``check()`` 的触发语义：
- ``""``（默认，skill）：空目录 digest 也是 ``""`` → 首次即 ``changed=False``，无内容不注入。
- ``None``（agent 覆盖）：哨兵，确保首次 ``check()`` 必触发一次加载——即便用户目录为空，
  也让依赖内置来源的加载（风格内置 agent）至少注入一次。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self

from lumi.utils.logger import logger


class FileSetChangeDetector[T]:
    """文件集变更检测器基类（单例，每个子类各自持有 ``_instance``）。"""

    _INITIAL_DIGEST: str | None = ""
    _instance: FileSetChangeDetector | None = None

    def __init__(self) -> None:
        self._cached_digest: str | None = self._INITIAL_DIGEST
        self._cached: list[T] = []

    # ------------------------------------------------------------------
    # 子类实现
    # ------------------------------------------------------------------

    def _iter_files(self) -> list[Path]:
        """返回参与变更检测的文件列表（目录不存在/扫描失败时返回空）。"""
        raise NotImplementedError

    def _key(self, path: Path) -> str:
        """文件在 digest 中的稳定标识，默认用文件名（扁平目录足够唯一）。"""
        return path.name

    def _load(self) -> list[T]:
        """重新加载并返回最新配置列表。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 公共逻辑
    # ------------------------------------------------------------------

    def _compute_digest(self) -> str:
        """对各文件 (key, mtime_ns, size) 排序后算 SHA-256；无文件返回 ``""``。"""
        entries: list[tuple[str, int, int]] = []
        for path in self._iter_files():
            try:
                stat = path.stat()
            except OSError:
                logger.warning("无法访问文件，已跳过: %s", path)
                continue
            entries.append((self._key(path), stat.st_mtime_ns, stat.st_size))

        if not entries:
            return ""

        entries.sort()
        hasher = hashlib.sha256()
        for entry in entries:
            hasher.update(repr(entry).encode())
        return hasher.hexdigest()

    def check(self) -> tuple[list[T], bool]:
        """检查是否变更，返回 ``(列表, 是否变更)``。"""
        current_digest = self._compute_digest()
        if current_digest == self._cached_digest:
            return list(self._cached), False

        self._cached = self._load()
        self._cached_digest = current_digest
        return list(self._cached), True

    # ------------------------------------------------------------------
    # 单例管理（按子类隔离）
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> Self:
        # 各子类显式声明了自己的 _instance 类属性，故 cls._instance 天然按子类隔离。
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        cls._instance = None

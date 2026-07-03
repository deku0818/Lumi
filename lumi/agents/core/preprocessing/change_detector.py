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
        # _seen_digest：check() 上次报告过的 digest（变更注入语义）；
        # _data_digest/_data：加载结果缓存（peek/check 共享，避免每次全量重解析）。
        self._seen_digest: str | None = self._INITIAL_DIGEST
        self._data_digest: str | None = None
        self._data: list[T] = []

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

    def _current(self) -> tuple[list[T], str]:
        """当前列表 + digest：digest 未变直接用缓存，变了才重新加载。"""
        digest = self._compute_digest()
        if digest != self._data_digest:
            self._data = self._load()
            self._data_digest = digest
        return self._data, digest

    def check(self) -> tuple[list[T], bool]:
        """检查是否变更，返回 ``(列表, 是否变更)``。"""
        data, digest = self._current()
        changed = digest != self._seen_digest
        self._seen_digest = digest
        return list(data), changed

    def peek(self) -> list[T]:
        """获取当前列表（只读，不影响 ``check()`` 的变更判断）。"""
        return list(self._current()[0])

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

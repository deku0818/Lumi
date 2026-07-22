"""文件集加载缓存基类

基于每个文件的 (key, mtime_ns, size) 算 SHA-256 digest 判断目录是否变更，
避免每轮都重新解析文件内容。子类只需提供「扫哪些文件 / 用什么 key / 如何加载」；
本类负责 digest 缓存与单例管理。「是否需要重新通知模型」的变更语义不在此处——
由 context_inject 的消息级 marker 承担（per-thread、随 checkpoint 持久）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self

from lumi.utils.logger import logger


class FileSetChangeDetector[T]:
    """文件集加载缓存基类（按 子类 × 项目 一实例，各子类自持 ``_instances``）。

    变更源是可变的 global + project 两层（目录集来自 loader.config_layers），
    风格内置视作只读不参与 digest。子类只声明 ``_subdir``/``_pattern`` 与 ``_load``。
    """

    _instances: dict[str, FileSetChangeDetector]
    _subdir: str  # 配置子目录名："skills" | "agents"
    _pattern: str  # 目录内定义文件的 glob 模式

    def __init__(
        self,
        project_dir: str | Path | None = None,
        explicit_dir: Path | None = None,
    ) -> None:
        # _data_digest/_data：加载结果缓存，digest 未变不重解析。
        # explicit_dir：只扫这一个目录且单层加载（测试用）；生产实例经 get_instance 创建
        self._project_dir = project_dir
        self._explicit_dir = explicit_dir
        self._data_digest: str | None = None
        self._data: list[T] = []

    # ------------------------------------------------------------------
    # 扫描（层序消费自 loader.config_layers，单一事实源）
    # ------------------------------------------------------------------

    def _scan_dirs(self) -> list[Path]:
        from lumi.agents.tools.loader import config_layers

        if self._explicit_dir:
            return [self._explicit_dir]
        return [
            layer
            for label, layer in config_layers(self._subdir, self._project_dir)
            if label != "builtin"
        ]

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        for scan_dir in self._scan_dirs():
            if not scan_dir.exists():
                continue
            try:
                files.extend(scan_dir.glob(self._pattern))
            except OSError:
                logger.warning("无法扫描配置目录: %s", scan_dir)
        return files

    def _key(self, path: Path) -> str:
        # 多目录集合：绝对路径才不会跨层撞 key
        return str(path)

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

    def peek(self) -> list[T]:
        """获取当前列表（digest 未变时走缓存）。"""
        return list(self._current()[0])

    # ------------------------------------------------------------------
    # 实例管理（按子类 × 项目隔离）
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, project_dir: str | Path | None = None) -> Self:
        """按项目取实例：变更检测的目录集随会话绑定的项目而不同，一项目一实例。

        各子类显式声明了自己的 ``_instances`` 类属性，故天然按子类隔离；
        ``project_dir=None``（无项目场景）共用 key 为空串的进程级实例。
        """
        key = str(project_dir) if project_dir else ""
        if key not in cls._instances:
            cls._instances[key] = cls(project_dir=project_dir)
        return cls._instances[key]

    @classmethod
    def reset(cls) -> None:
        """重置全部实例（测试用）。"""
        cls._instances.clear()

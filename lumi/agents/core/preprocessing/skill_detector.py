"""技能变更检测器模块

基于文件 mtime + size 的 SHA-256 digest 判断 ``.skills/`` 目录是否发生变更，
避免每次都重新解析文件内容。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from lumi.agents.tools.loader import SkillConfig, load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class SkillChangeDetector:
    """技能变更检测器（单例）。

    通过比较 ``SKILL.md`` 文件元信息的 digest 判断是否需要重新加载。
    """

    _instance: SkillChangeDetector | None = None

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir: Path = skills_dir or get_config().skills_dir
        self._cached_digest: str = ""
        self._cached_skills: list[SkillConfig] = []

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    # ------------------------------------------------------------------
    # digest 计算
    # ------------------------------------------------------------------

    def _compute_digest(self) -> str:
        """收集每个 SKILL.md 的 (路径, mtime_ns, size) 并计算 SHA-256 摘要。"""
        try:
            skill_files = list(self._skills_dir.rglob("SKILL.md"))
        except OSError:
            logger.warning("无法扫描技能目录: %s", self._skills_dir)
            return ""

        entries: list[tuple[str, int, int]] = []
        for path in skill_files:
            try:
                stat = path.stat()
                entries.append(
                    (
                        str(path.relative_to(self._skills_dir)),
                        stat.st_mtime_ns,
                        stat.st_size,
                    )
                )
            except OSError:
                logger.warning("无法访问技能文件，已跳过: %s", path)

        if not entries:
            return ""

        entries.sort()
        hasher = hashlib.sha256()
        for entry in entries:
            hasher.update(repr(entry).encode())
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def peek(self) -> list[SkillConfig]:
        """获取当前技能列表（只读，不影响 ``check()`` 的变更判断）。"""
        if not self._skills_dir.exists():
            return []
        return load_skills(directory=str(self._skills_dir))

    def check(self) -> tuple[list[SkillConfig], bool]:
        """检查技能是否变更，返回 ``(技能列表, 是否变更)``。"""
        if not self._skills_dir.exists():
            return [], False

        current_digest = self._compute_digest()
        if current_digest == self._cached_digest:
            return list(self._cached_skills), False

        self._cached_skills = load_skills(directory=str(self._skills_dir))
        self._cached_digest = current_digest
        return list(self._cached_skills), True

    # ------------------------------------------------------------------
    # 单例管理
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> SkillChangeDetector:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        cls._instance = None

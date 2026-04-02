"""技能变更检测器模块

基于文件 mtime 和 size 的 digest 哈希判断 .skills/ 目录下的技能是否发生变更，
避免每次都重新解析文件内容。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from lumi.agents.tools.loader import SkillConfig, load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class SkillChangeDetector:
    """技能变更检测器，基于文件 mtime 和 size 的 digest 哈希判断技能是否变更。

    使用 get_instance() / reset() 类方法进行单例管理，
    避免可变全局状态，同时方便测试替换。
    """

    _instance: SkillChangeDetector | None = None

    def __init__(self, skills_dir: Path | None = None) -> None:
        """初始化检测器。

        Args:
            skills_dir: 技能目录路径，为 None 时从全局配置获取。
        """
        self._skills_dir = skills_dir or get_config().skills_dir
        self._cached_digest: str = ""
        self._cached_skills: list[SkillConfig] = []

    @property
    def skills_dir(self) -> Path:
        """技能目录路径。"""
        return self._skills_dir

    def _compute_digest(self) -> str:
        """计算 .skills/ 目录下所有 SKILL.md 的 digest。

        收集每个 SKILL.md 的 (相对路径, mtime_ns, size) 元组，
        排序后用 hashlib.sha256 计算十六进制摘要。
        遇到不可访问的文件时捕获 OSError 并跳过。

        Returns:
            十六进制摘要字符串，目录为空或不存在时返回空字符串。
        """
        entries: list[tuple[str, int, int]] = []

        try:
            skill_files = list(self._skills_dir.rglob("SKILL.md"))
        except OSError:
            logger.warning("无法扫描技能目录: %s", self._skills_dir)
            return ""

        for path in skill_files:
            try:
                stat = path.stat()
                rel = str(path.relative_to(self._skills_dir))
                entries.append((rel, stat.st_mtime_ns, stat.st_size))
            except OSError:
                logger.warning("无法访问技能文件，已跳过: %s", path)

        if not entries:
            return ""

        entries.sort()
        hasher = hashlib.sha256()
        for entry in entries:
            hasher.update(repr(entry).encode())
        return hasher.hexdigest()

    def peek(self) -> list[SkillConfig]:
        """获取当前技能列表，不更新变更检测状态。

        供 TUI 等只需读取技能列表的场景使用，
        不会影响 check() 的 changed 判断。

        Returns:
            技能配置列表
        """
        if not self._skills_dir.exists():
            return []
        return load_skills(directory=str(self._skills_dir))

    def check(self) -> tuple[list[SkillConfig], bool]:
        """检查技能是否变更。

        比较当前 digest 与缓存值：
        - 变更时调用 load_skills() 重新加载并更新缓存
        - 未变更时返回缓存列表
        - 目录不存在时返回空列表

        Returns:
            (技能列表, 是否发生变更)
        """
        if not self._skills_dir.exists():
            return [], False

        current_digest = self._compute_digest()

        if current_digest == self._cached_digest:
            return list(self._cached_skills), False

        # digest 变更，重新加载
        self._cached_skills = load_skills(directory=str(self._skills_dir))
        self._cached_digest = current_digest
        return list(self._cached_skills), True

    @classmethod
    def get_instance(cls) -> SkillChangeDetector:
        """获取全局单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（用于测试）。"""
        cls._instance = None

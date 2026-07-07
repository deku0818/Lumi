"""技能加载缓存模块

基于 ``.skills/`` 目录下各 ``SKILL.md`` 的 mtime + size digest 缓存加载结果。
通用 digest/缓存/单例逻辑见 [[change_detector]] 的 FileSetChangeDetector。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.core.preprocessing.change_detector import FileSetChangeDetector
from lumi.agents.tools.loader import SkillConfig, load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class SkillChangeDetector(FileSetChangeDetector[SkillConfig]):
    """技能加载缓存（单例）。"""

    _instance: SkillChangeDetector | None = None

    def __init__(self, skills_dir: Path | None = None) -> None:
        super().__init__()
        self._skills_dir: Path = skills_dir or get_config().skills_dir

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def _iter_files(self) -> list[Path]:
        if not self._skills_dir.exists():
            return []
        try:
            return list(self._skills_dir.rglob("SKILL.md"))
        except OSError:
            logger.warning("无法扫描技能目录: %s", self._skills_dir)
            return []

    def _key(self, path: Path) -> str:
        return str(path.relative_to(self._skills_dir))

    def _load(self) -> list[SkillConfig]:
        return load_skills(directory=str(self._skills_dir))

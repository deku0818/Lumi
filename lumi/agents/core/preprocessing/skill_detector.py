"""技能加载缓存模块

扫描/digest/实例管理全部在 [[change_detector]] 的 FileSetChangeDetector 基类；
本类只声明目录形态（``skills/<name>/SKILL.md``）与加载函数。
"""

from __future__ import annotations

from lumi.agents.core.preprocessing.change_detector import FileSetChangeDetector
from lumi.agents.tools.loader import SkillConfig, load_skills


class SkillChangeDetector(FileSetChangeDetector[SkillConfig]):
    """技能加载缓存（按项目一实例，见基类 get_instance）。"""

    _instances: dict[str, SkillChangeDetector] = {}
    _subdir = "skills"
    _pattern = "*/SKILL.md"

    def _load(self) -> list[SkillConfig]:
        if self._explicit_dir:
            return load_skills(directory=str(self._explicit_dir))
        return load_skills(project_dir=self._project_dir)

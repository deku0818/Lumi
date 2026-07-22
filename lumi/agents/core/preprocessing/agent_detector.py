"""Agent 加载缓存模块

扫描/digest/实例管理全部在 [[change_detector]] 的 FileSetChangeDetector 基类；
本类只声明目录形态（``agents/*.md``）与加载函数。
"""

from __future__ import annotations

from lumi.agents.core.preprocessing.change_detector import FileSetChangeDetector
from lumi.agents.tools.loader import AgentConfig, load_agents


class AgentChangeDetector(FileSetChangeDetector[AgentConfig]):
    """Agent 加载缓存（按项目一实例，见基类 get_instance）。"""

    _instances: dict[str, AgentChangeDetector] = {}
    _subdir = "agents"
    _pattern = "*.md"

    def _load(self) -> list[AgentConfig]:
        if self._explicit_dir:
            return load_agents(directory=str(self._explicit_dir))
        return load_agents(project_dir=self._project_dir)

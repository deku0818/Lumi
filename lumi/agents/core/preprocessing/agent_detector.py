"""Agent 变更检测器模块

基于用户 ``.lumi/agents/`` 下各 ``*.md`` 的 mtime + size digest 判断是否变更。
风格内置 agent 在进程内静态、不参与变更检测——故 ``_INITIAL_DIGEST=None``（哨兵）
保证首次 ``check()`` 必触发一次加载，使内置 agent 也能被注入。
通用 digest/缓存/单例逻辑见 [[change_detector]] 的 FileSetChangeDetector。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.core.preprocessing.change_detector import FileSetChangeDetector
from lumi.agents.tools.loader import AgentConfig, load_agents
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class AgentChangeDetector(FileSetChangeDetector[AgentConfig]):
    """Agent 变更检测器（单例）。

    digest 只扫用户目录（变更源）；``check()`` 加载的是「风格内置 + 用户」合并列表。
    """

    _INITIAL_DIGEST: str | None = None
    _instance: AgentChangeDetector | None = None

    def __init__(self, agents_dir: Path | None = None) -> None:
        super().__init__()
        self._agents_dir: Path = agents_dir or get_config().agents_dir

    @property
    def agents_dir(self) -> Path:
        return self._agents_dir

    def _iter_files(self) -> list[Path]:
        if not self._agents_dir.exists():
            return []
        try:
            return list(self._agents_dir.glob("*.md"))
        except OSError:
            logger.warning("无法扫描 agent 目录: %s", self._agents_dir)
            return []

    def _load(self) -> list[AgentConfig]:
        return load_agents(directory=str(self._agents_dir))

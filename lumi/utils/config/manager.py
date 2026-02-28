"""配置管理器

提供 LumiConfig 配置管理类和 get_config 便捷函数。
"""

import json
from pathlib import Path
from typing import Optional

import yaml

from lumi.utils.config.discovery import ConfigDiscovery
from lumi.utils.config.models import Config
from lumi.utils.logger import logger


class LumiConfig:
    """Lumi 配置管理器

    支持从动态发现的配置目录加载配置，提供各种配置路径的访问方法。
    """

    _instance: Optional["LumiConfig"] = None

    def __init__(self, config_dir: str | None = None):
        """初始化配置管理器

        Args:
            config_dir: 可选的配置目录路径，如果不指定则自动发现
        """
        self.discovery = ConfigDiscovery(config_dir)
        self._config: Config | None = None
        self._config_dir: Path | None = None

    @classmethod
    def get_instance(
        cls, config_dir: str | None = None, reset: bool = False
    ) -> "LumiConfig":
        """获取全局单例实例

        Args:
            config_dir: 可选的配置目录路径
            reset: 是否重置单例实例

        Returns:
            LumiConfig 单例实例
        """
        if cls._instance is None or config_dir or reset:
            cls._instance = cls(config_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例实例（主要用于测试）"""
        cls._instance = None

    @property
    def config_dir(self) -> Path:
        """获取配置目录路径"""
        if self._config_dir is None:
            self._config_dir = self.discovery.discover()
        return self._config_dir

    @property
    def config(self) -> Config:
        """获取配置对象"""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> Config:
        """加载配置文件

        Returns:
            Config: 配置对象，如果配置文件不存在则返回默认配置
        """
        config_file = self.config_dir / "config.yaml"

        if not config_file.exists():
            return Config()

        with open(config_file, encoding="utf-8") as f:
            config_dict = yaml.safe_load(f) or {}

        return Config(**config_dict)

    # === 目录路径属性 ===

    @property
    def skills_dir(self) -> Path:
        """获取 skills 目录路径"""
        return self.config_dir / "skills"

    @property
    def agents_dir(self) -> Path:
        """获取 agents 目录路径"""
        return self.config_dir / "agents"

    @property
    def prompts_dir(self) -> Path:
        """获取 prompts 目录路径"""
        return self.config_dir / "prompts"

    @property
    def mcp_config_path(self) -> Path:
        """获取 MCP 配置文件路径"""
        return self.config_dir / "mcp_server.json"

    # === 配置加载方法 ===

    def load_mcp_config(self) -> dict:
        """加载 MCP 配置

        Returns:
            MCP 配置字典，如果文件不存在返回空字典
        """
        if not self.mcp_config_path.exists():
            return {}
        with open(self.mcp_config_path, encoding="utf-8") as f:
            return json.load(f)

    def load_system_prompt(self) -> str:
        """加载三文件组合提示词 (SOUL.md + GUARDRAILS.md + AGENTS.md)

        每部分用对应的 XML 标签包裹，优先使用三文件组合，
        如果无有效内容则回退到 simple.md。

        Raises:
            ValueError: 未找到任何提示词配置
        """
        parts = []
        for name in ["SOUL", "GUARDRAILS", "AGENTS"]:
            content = self.load_prompt(name)
            if content:
                parts.append(f"<{name}>\n{content}\n</{name}>")

        if parts:
            return "\n\n".join(parts)

        # 三文件无有效内容，回退到 simple.md
        prompt = self.load_prompt("simple")
        if prompt:
            logger.warning(
                "使用 simple.md 作为系统提示词已废弃，"
                "请将提示词迁移到 prompts/SOUL.md, GUARDRAILS.md, AGENTS.md。\n"
                "运行 `omniagent init --update` 生成模板文件。"
            )
            return prompt

        raise ValueError(
            "未找到提示词配置。\n"
            "请在 .lumi/prompts/ 中配置 SOUL.md, GUARDRAILS.md, AGENTS.md。"
        )

    def load_prompt(self, name: str) -> str | None:
        """加载自定义提示词

        Args:
            name: 提示词文件名（不包含后缀）

        Returns:
            提示词内容，如果文件不存在返回 None
        """
        prompt_file = self.prompts_dir / f"{name}.md"
        if not prompt_file.exists():
            return None

        content = prompt_file.read_text(encoding="utf-8")

        # 解析 Markdown frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                # 返回 frontmatter 之后的内容
                return parts[2].strip()

        return content

    def ensure_dirs(self):
        """确保所有配置目录存在"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(exist_ok=True)
        self.agents_dir.mkdir(exist_ok=True)

    def exists(self) -> bool:
        """检查配置目录是否存在"""
        return self.config_dir.exists()


def get_config(config_dir: str | None = None) -> LumiConfig:
    """获取配置实例的便捷函数

    Args:
        config_dir: 可选的配置目录路径

    Returns:
        LumiConfig 实例
    """
    return LumiConfig.get_instance(config_dir)

"""配置管理器

提供 LumiConfig 配置管理类和 get_config 便捷函数。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from lumi.utils.config.discovery import ConfigDiscovery
from lumi.utils.config.models import Config
from lumi.utils.logger import logger


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 Markdown frontmatter → ``(metadata dict, 正文)``。

    要求首行恰为 ``---``（容忍 BOM 和开头空白）且后续有一行**独立成行**的 ``---``
    作闭合；正文里作分隔线用的 ``---`` 不会被误判。无 frontmatter 时返回
    ``({}, 正文strip)``；YAML 解析失败或非 dict 也返回空 metadata，不抛。
    Agent/Skill 加载与记忆索引规范化共用同一套解析。
    """
    lines = content.lstrip("﻿ \t\r\n").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                try:
                    meta = yaml.safe_load("\n".join(lines[1:i])) or {}
                except yaml.YAMLError as e:
                    logger.warning(f"frontmatter YAML 解析失败: {e}")
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                return meta, "\n".join(lines[i + 1 :]).strip()
    return {}, content.strip()


def strip_frontmatter(content: str) -> str:
    """剥离 Markdown 文件开头的 frontmatter，返回正文（见 :func:`parse_frontmatter`）。

    frontmatter 仅用于标识/给人看（name、description 等），不应进入提示词。
    """
    return parse_frontmatter(content)[1]


class LumiConfig:
    """Lumi 配置管理器

    支持从动态发现的配置目录加载配置，提供各种配置路径的访问方法。
    """

    _instance: LumiConfig | None = None

    def __init__(self, config_dir: str | None = None):
        """初始化配置管理器

        Args:
            config_dir: 可选的配置目录路径，如果不指定则自动发现
        """
        self.discovery = ConfigDiscovery(config_dir)
        self._config: Config | None = None
        self._config_dir: Path | None = None
        self._style_override: str | None = None

    def set_style_override(self, style: str) -> None:
        """设置风格覆盖（CLI --style 参数优先于 config.json）"""
        self._style_override = style

    @property
    def active_style(self) -> str:
        """当前生效的风格名称。优先级：CLI override > config.json > "default" """
        if self._style_override is not None:
            return self._style_override
        return self.config.style

    @classmethod
    def get_instance(
        cls, config_dir: str | None = None, reset: bool = False
    ) -> LumiConfig:
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
        config_file = self.config_dir / "config.json"

        if not config_file.exists():
            return Config()

        try:
            config_dict = json.loads(config_file.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError as e:
            logger.error(f"config.json 格式错误，使用默认配置: {e}")
            return Config()
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"config.json 读取失败，使用默认配置: {e}")
            return Config()

        try:
            return Config(**config_dict)
        except ValueError as e:
            logger.error(f"config.json 字段校验失败，使用默认配置: {e}")
            return Config()

    def apply_env(self) -> None:
        """将配置中的 env 字段注入到 os.environ

        config.json 中的 env 优先级高于系统环境变量，始终覆盖。
        """
        for key, value in self.config.env.items():
            os.environ[key] = str(value)
            logger.debug(f"注入环境变量: {key}")

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
        """加载双文件组合提示词 (SOUL.md + AGENTS.md)

        加载顺序：先从 style 内置目录读取基础文件，
        再用用户 .lumi/prompts/ 下的同名文件覆盖。
        各部分按顺序直接拼接（不做 XML 包裹）。
        无内置 prompts 的风格（如 default）提示词全部来自 .lumi/prompts/；
        若两处都没有，返回空串（以无系统提示词运行），不再 fail-loud。

        Raises:
            ValueError: 提示词文件存在但读取失败
        """
        from lumi.styles import get_style_prompts_dir

        style = self.active_style
        file_names = ["SOUL", "AGENTS"]

        # 按 name 收集最终路径：style 内置 → 用户覆盖
        resolved: dict[str, Path] = {}

        # 1. style 内置（风格无 prompts/ 目录时跳过，提示词全部来自 .lumi/）
        try:
            style_dir = get_style_prompts_dir(style)
            for name in file_names:
                path = style_dir / f"{name}.md"
                if path.exists():
                    resolved[name] = path
        except ValueError:
            logger.debug(
                f"风格 '{style}' 无内置 prompts，提示词全部来自 .lumi/prompts/"
            )

        # 2. 用户 .lumi/prompts/ 覆盖
        for name in file_names:
            user_path = self.prompts_dir / f"{name}.md"
            if user_path.exists():
                resolved[name] = user_path

        # 按 file_names 顺序直接拼接（不做 XML 包裹）
        parts: list[str] = []
        for name in file_names:
            path = resolved.get(name)
            if path is None:
                continue
            try:
                content = strip_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as e:
                raise ValueError(f"提示词文件读取失败: {path} ({e})") from e
            if content:
                parts.append(content)

        if parts:
            logger.info(f"使用风格 '{style}' 的系统提示词")
            return "\n\n".join(parts)

        # 无内置 prompts 的风格（如 default）且用户未配置 .lumi/prompts/：
        # 以空系统提示词运行（call_model 的 `if system_prompt:` 会跳过 SystemMessage）。
        logger.info(f"风格 '{style}' 无提示词配置，以空系统提示词运行")
        return ""

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

        return strip_frontmatter(prompt_file.read_text(encoding="utf-8"))

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

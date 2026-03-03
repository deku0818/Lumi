"""全局配置管理器

负责 ~/.lumi/lumi.json 的读取、写入和初始化。
所有方法为静态方法，无实例状态。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lumi.utils.logger import logger

from .global_models import GlobalConfig

# 路径常量：固定为 ~/.lumi/，不受命令行参数影响
GLOBAL_CONFIG_DIR: Path = Path.home() / ".lumi"
GLOBAL_CONFIG_FILE: Path = GLOBAL_CONFIG_DIR / "lumi.json"


class GlobalConfigManager:
    """全局配置管理器

    负责 ~/.lumi/lumi.json 的读取、写入和初始化。
    所有方法为静态方法，无实例状态。
    """

    @staticmethod
    def load() -> GlobalConfig:
        """加载全局配置，文件不存在时自动创建。

        Returns:
            GlobalConfig 实例。读取失败时返回默认配置。
        """
        try:
            GlobalConfigManager._ensure_dir()
        except PermissionError:
            logger.error("无法创建 ~/.lumi/ 目录")
            return GlobalConfig()

        if not GLOBAL_CONFIG_FILE.exists():
            config = GlobalConfig()
            try:
                GlobalConfigManager.save(config)
            except Exception:
                logger.error("无法写入默认配置文件")
            return config

        try:
            data = json.loads(GLOBAL_CONFIG_FILE.read_text("utf-8"))
            return GlobalConfig(**data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("lumi.json 解析失败，使用默认配置")
            return GlobalConfig()

    @staticmethod
    def save(config: GlobalConfig) -> None:
        """原子写入全局配置到 ~/.lumi/lumi.json。

        先写入临时文件再用 os.replace() 原子替换，
        写入失败时清理临时文件并抛出异常，原文件不受影响。

        Args:
            config: 要保存的全局配置实例。

        Raises:
            Exception: 写入或替换失败时抛出原始异常。
        """
        GlobalConfigManager._ensure_dir()
        tmp_path = GLOBAL_CONFIG_FILE.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                config.model_dump_json(indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, GLOBAL_CONFIG_FILE)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _ensure_dir() -> None:
        """确保 ~/.lumi/ 目录存在。

        Raises:
            PermissionError: 目录创建权限不足时抛出。
        """
        GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

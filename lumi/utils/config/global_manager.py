"""全局配置管理器

负责 ~/.lumi/lumi.json 中 "settings" 分区的读取与写入（经 user_store）。
所有方法为静态方法，无实例状态。
"""

from __future__ import annotations

from pathlib import Path

from lumi.utils.logger import logger

from . import user_store
from .global_models import GlobalConfig

# 路径常量：固定为 ~/.lumi/，不受命令行参数影响（cron / catalog / uploads 共用）
GLOBAL_CONFIG_DIR: Path = Path.home() / ".lumi"


def uploads_dir() -> Path:
    """上传图片的集中存放目录（``~/.lumi/uploads/``）。

    集中存放、不污染用户项目。read / vision 为只读工具、不受工作区边界限制
    （见 permissions.routing 的只读免边界规则），故能直接读取此目录。
    """
    return GLOBAL_CONFIG_DIR / "uploads"


class GlobalConfigManager:
    """全局配置管理器

    负责 ~/.lumi/lumi.json 中 "settings" 分区的读写（委托 user_store）。
    所有方法为静态方法，无实例状态。
    """

    @staticmethod
    def load() -> GlobalConfig:
        """加载全局配置（lumi.json 的 "settings" 分区）；缺失/损坏返回默认配置。"""
        data = user_store.read_section("settings", {})  # read_section 已保证 dict 类型
        try:
            return GlobalConfig(**data)
        except ValueError as e:
            logger.warning(f"settings 字段校验失败，使用默认配置: {e}")
            return GlobalConfig()

    @staticmethod
    def save(config: GlobalConfig) -> None:
        """写入全局配置到 lumi.json 的 "settings" 分区（经 user_store 原子写）。

        Raises:
            OSError: 写入或替换失败时抛出。
        """
        user_store.write_section("settings", config.model_dump())

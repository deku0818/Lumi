"""配置目录发现模块

配置发现优先级（从高到低）：
1. 命令行参数: --config-dir /path/to/config
2. 环境变量: LUMI_CONFIG_DIR
3. 当前目录查找: .lumi/
4. 用户主目录: ~/.lumi/
5. 默认值: 当前目录的 .lumi/ (即使不存在)
"""

import os
from pathlib import Path


class ConfigDiscovery:
    """配置目录发现器"""

    CONFIG_DIR_NAME = ".lumi"
    ENV_VAR = "LUMI_CONFIG_DIR"

    def __init__(self, cli_config_dir: str | None = None):
        """初始化配置发现器

        Args:
            cli_config_dir: 命令行指定的配置目录路径
        """
        self.cli_config_dir = cli_config_dir
        self._cached_config_dir: Path | None = None
        # 在初始化时缓存当前工作目录，避免在异步上下文中阻塞
        self._cwd = Path.cwd()

    def discover(self) -> Path:
        """发现配置目录，返回绝对路径

        Returns:
            配置目录的绝对路径
        """
        if self._cached_config_dir is not None:
            return self._cached_config_dir

        # 1. 命令行参数
        if self.cli_config_dir:
            self._cached_config_dir = Path(self.cli_config_dir).resolve()
            return self._cached_config_dir

        # 2. 环境变量
        env_dir = os.getenv(self.ENV_VAR)
        if env_dir:
            self._cached_config_dir = Path(env_dir).resolve()
            return self._cached_config_dir

        # 3. 当前目录查找
        cwd_config = self._cwd / self.CONFIG_DIR_NAME
        if cwd_config.is_dir():
            self._cached_config_dir = cwd_config
            return self._cached_config_dir

        # 4. 用户主目录
        home_config = Path.home() / self.CONFIG_DIR_NAME
        if home_config.exists():
            self._cached_config_dir = home_config
            return self._cached_config_dir

        # 5. 返回当前目录的 .lumi (即使不存在)
        self._cached_config_dir = self._cwd / self.CONFIG_DIR_NAME
        return self._cached_config_dir

    def get_config_file_path(self) -> Path:
        """获取配置文件路径

        Returns:
            config.yaml 的路径
        """
        return self.discover() / "config.yaml"

    def exists(self) -> bool:
        """检查配置目录是否存在

        Returns:
            配置目录是否存在
        """
        config_dir = self.discover()
        return config_dir.exists()

    def clear_cache(self):
        """清除缓存的配置目录路径"""
        self._cached_config_dir = None

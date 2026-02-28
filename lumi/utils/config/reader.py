"""配置文件读取器

提供 YAML/JSON 配置文件的读取和解析功能。
支持在配置值中使用 ${ENV_VAR} 语法引用环境变量。
"""

import json
import os
import re
from pathlib import Path

import yaml


class MissingEnvVarError(Exception):
    """环境变量缺失异常"""

    def __init__(self, env_var: str):
        self.env_var = env_var
        super().__init__(f"环境变量 '{env_var}' 未设置且无默认值")


def _expand_env_vars(value):
    """递归展开配置值中的环境变量引用

    支持 ${ENV_VAR} 和 ${ENV_VAR:-default} 语法。

    Args:
        value: 配置值，可以是字符串、字典或列表

    Returns:
        展开环境变量后的值

    Raises:
        MissingEnvVarError: 当环境变量不存在且未提供默认值时
    """
    if isinstance(value, str):
        # 匹配 ${VAR} 或 ${VAR:-default}
        pattern = r"\$\{([^}:]+)(?::-([^}]*))?\}"

        def replacer(match):
            env_var = match.group(1)
            default = match.group(2)
            env_value = os.getenv(env_var)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            raise MissingEnvVarError(env_var)

        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    else:
        return value


def load_yaml_config(path: Path) -> dict:
    """加载 YAML 配置文件，自动展开环境变量

    Args:
        path: 配置文件路径

    Returns:
        展开环境变量后的配置字典

    Raises:
        FileNotFoundError: 配置文件不存在
        yaml.YAMLError: YAML 解析错误
        MissingEnvVarError: 环境变量不存在且未提供默认值
    """
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return _expand_env_vars(config)


def load_json_config(path: Path) -> dict:
    """加载 JSON 配置文件，自动展开环境变量

    Args:
        path: 配置文件路径

    Returns:
        展开环境变量后的配置字典

    Raises:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: JSON 解析错误
        MissingEnvVarError: 环境变量不存在且未提供默认值
    """
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    return _expand_env_vars(config)

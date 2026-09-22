"""按工作目录的会话级环境注入（provider 由 gateway 在 serve 启动时注册）。

shell 会话、后台 Bash 任务、技能嵌入命令三条 spawn 路径共用同一份注入。
agents 层不 import gateway，靠注册倒转依赖；未注册（纯 CLI 等场景）不注入。
"""

from __future__ import annotations

from collections.abc import Callable

from lumi.utils.logger import logger

# working_dir → 额外环境变量（如项目专属飞书机器人的 LARKSUITE_CLI_PROFILE）
_env_provider: Callable[[str], dict[str, str]] | None = None


def set_shell_env_provider(provider: Callable[[str], dict[str, str]]) -> None:
    """注册会话环境 provider：``fn(working_dir) -> dict[str, str]``。"""
    global _env_provider
    _env_provider = provider


def provided_env(working_dir: str) -> dict[str, str]:
    """当前 provider 对该工作目录的注入项；未注册/失败返回空（不阻断 shell 启动）。"""
    if _env_provider is None:
        return {}
    try:
        return _env_provider(working_dir) or {}
    except Exception:
        logger.warning("shell env provider 执行失败，跳过注入", exc_info=True)
        return {}

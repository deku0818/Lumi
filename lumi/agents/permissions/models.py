"""工具权限控制系统 - 数据模型定义

定义权限系统所需的所有枚举、frozen dataclass 和常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 工具参数中表示文件路径的键名（按优先顺序匹配），供路径规则匹配使用
PATH_ARG_KEYS: tuple[str, ...] = ("file_path", "path")


class Permission(Enum):
    """权限类型"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionDecision(Enum):
    """权限评估决策结果"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class PermissionRule:
    """单条权限规则（不可变）

    Attributes:
        tool: 工具表达式，如 "bash(npm *)"
        permission: allow、deny 或 ask
    """

    tool: str
    permission: Permission


@dataclass(frozen=True)
class PermissionConfig:
    """权限配置（不可变）

    Attributes:
        permissions: 权限规则元组
    """

    permissions: tuple[PermissionRule, ...] = ()


DEFAULT_RULES: tuple[PermissionRule, ...] = (
    PermissionRule(tool="agent_list", permission=Permission.ALLOW),
    # 框架结构化输出控制流；与其他规则一样可被用户 ask / deny 覆盖。
    PermissionRule(tool="__structured_output__", permission=Permission.ALLOW),
    PermissionRule(tool="cron", permission=Permission.ALLOW),
    # 只呈现文件，不改动文件
    PermissionRule(tool="artifacts", permission=Permission.ALLOW),
)

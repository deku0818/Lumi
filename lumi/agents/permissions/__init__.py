"""工具权限控制系统

提供基于配置文件的工具权限管理，支持 allow/deny 规则匹配、
工作区边界保护和多级配置加载。
"""

from lumi.agents.permissions.boundary import WorkspaceBoundary
from lumi.agents.permissions.config_loader import ConfigLoader
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.permissions.matcher import (
    RuleMatcher,
    build_exact_expr,
    build_pattern_expr,
)
from lumi.agents.permissions.models import (
    DEFAULT_RULES,
    Permission,
    PermissionConfig,
    PermissionDecision,
    PermissionRule,
)

__all__ = [
    "ConfigLoader",
    "DEFAULT_RULES",
    "Permission",
    "PermissionConfig",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionRule",
    "RuleMatcher",
    "build_exact_expr",
    "build_pattern_expr",
    "WorkspaceBoundary",
]

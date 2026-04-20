"""工具权限控制系统

提供基于配置文件的工具权限管理，支持 allow/deny 规则匹配、
工作区边界保护和多级配置加载。
"""

from lumi.agents.permissions.boundary import WorkspaceBoundary
from lumi.agents.permissions.config_loader import ConfigLoader
from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.tools.capability import split_compound_command
from lumi.agents.permissions.matcher import (
    RuleMatcher,
    build_exact_expr,
    build_pattern_expr,
)
from lumi.agents.permissions.safety import is_bypass_immune
from lumi.agents.permissions.models import (
    BYPASS_TOOLS,
    DEFAULT_RULES,
    ApprovalOption,
    ApprovalRequest,
    Permission,
    PermissionConfig,
    PermissionDecision,
    PermissionRule,
    ToolCallInfo,
)

__all__ = [
    "BYPASS_TOOLS",
    "ConfigLoader",
    "DEFAULT_RULES",
    "ApprovalOption",
    "ApprovalRequest",
    "Permission",
    "PermissionConfig",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionRule",
    "RuleMatcher",
    "build_exact_expr",
    "build_pattern_expr",
    "ToolCallInfo",
    "WorkspaceBoundary",
    "is_bypass_immune",
    "split_compound_command",
]

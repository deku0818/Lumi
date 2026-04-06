"""工具权限控制系统 - 数据模型定义

定义权限系统所需的所有枚举、frozen dataclass 和常量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
        permission: allow 或 deny
    """

    tool: str
    permission: Permission


@dataclass(frozen=True)
class PermissionConfig:
    """权限配置（不可变）

    Attributes:
        workspaces: 工作区目录路径列表
        permissions: 权限规则元组
    """

    workspaces: tuple[str, ...] = ()
    permissions: tuple[PermissionRule, ...] = ()


@dataclass(frozen=True)
class ToolCallInfo:
    """工具调用信息（用于批量评估）

    Attributes:
        name: 工具名称
        args: 工具参数
    """

    name: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalOption:
    """审批选项

    Attributes:
        key: 选项标识，如 allow_once, always_allow_exact, always_allow_pattern, reject
        label: 显示文本
        tool_expr: always_allow_* 时的工具表达式
    """

    key: str
    label: str
    tool_expr: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    """审批请求（传递给 interrupt）

    Attributes:
        type: 请求类型，如 "tool_approval"
        tool_calls: 待审批的工具调用
        decisions: 各工具的权限决策
        options: 可选操作
        warnings: deny 警告信息
        boundary_violations: 工作区越界路径
    """

    type: str
    tool_calls: list[dict]
    decisions: list[PermissionDecision]
    options: list[ApprovalOption]
    warnings: list[str] = field(default_factory=list)
    boundary_violations: list[str] = field(default_factory=list)


# 兼容性保留，新代码应使用 capability.is_write_tool()
BYPASS_TOOLS: frozenset[str] = frozenset(
    {
        "ask",
        "read",
        "glob",
        "grep",
        "todos",
        "skill",
        "agent",
        "EnterPlanMode",
        "ExitPlanMode",
    }
)

DEFAULT_RULES: tuple[PermissionRule, ...] = (
    PermissionRule(tool="cron", permission=Permission.ALLOW),
)

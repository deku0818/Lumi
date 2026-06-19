"""执行模式策略 — 基于当前执行模式的工具限制

每种执行模式（plan / readonly / 自定义）对应一个 ModePolicy 实例，声明：
- allow_write: 是否允许写入操作
- path_filter: 写入操作的路径白名单（仅 allow_write=False 时生效）

"normal" 模式无策略限制（policy 为 None），工具调用直接走后续权限引擎。

扩展方式：调用 register_policy("my_mode", ModePolicy(...)) 注册自定义模式。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.tools.capability import is_write_tool

if TYPE_CHECKING:
    from langchain_core.tools.structured import StructuredTool


# ── 数据模型 ──


@dataclass(frozen=True)
class ModePolicy:
    """执行模式的工具限制策略

    Attributes:
        name: 模式标识符，如 "plan", "readonly"
        label: 显示名称，用于拒绝消息（如 "Plan mode"）
        allow_write: 是否允许写入操作。
            True → 不限制写入（等同于无策略）。
            False → 写入操作被拦截，除非 path_filter 放行。
        path_filter: 写入操作的路径白名单函数。
            仅在 allow_write=False 时生效。
            None → 不允许任何写入。
            返回 True → 该路径允许写入。
    """

    name: str
    label: str
    allow_write: bool = True
    path_filter: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class PolicyResult:
    """策略评估结果"""

    allowed: bool
    reason: str = ""


# ── 路径检查工具 ──


def _is_under_lumi_plans(file_path: str) -> bool:
    """检查路径是否为 .lumi/plans/ 下的 .md 文件"""
    if not file_path:
        return False
    try:
        p = Path(file_path).expanduser().resolve()
    except (RuntimeError, OSError):
        return False
    return "/.lumi/plans/" in p.as_posix() and p.suffix == ".md"


# ── 内置策略 ──


PLAN_POLICY = ModePolicy(
    name="plan",
    label="Plan mode",
    allow_write=False,
    path_filter=_is_under_lumi_plans,
)

READONLY_POLICY = ModePolicy(
    name="readonly",
    label="Read-only mode",
    allow_write=False,
    path_filter=None,
)

# ── 策略注册表 ──

_POLICIES: dict[str, ModePolicy | None] = {
    "normal": None,
    "plan": PLAN_POLICY,
    "readonly": READONLY_POLICY,
}


def get_policy(mode: str) -> ModePolicy | None:
    """获取执行模式对应的策略

    Args:
        mode: 模式标识符

    Returns:
        ModePolicy 实例，"normal" 或未知模式返回 None（无限制）
    """
    return _POLICIES.get(mode)


def register_policy(mode: str, policy: ModePolicy) -> None:
    """注册自定义执行模式策略

    Args:
        mode: 模式标识符
        policy: 策略实例
    """
    _POLICIES[mode] = policy


# ── 策略守卫 ──


def check_policy(policy: ModePolicy, tool_name: str, tool_args: dict) -> PolicyResult:
    """检查工具调用是否被模式策略允许

    Args:
        policy: 当前模式的策略
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        PolicyResult，allowed=False 时 reason 说明拒绝原因
    """
    # 允许写入的策略 → 全部放行
    if policy.allow_write:
        return PolicyResult(allowed=True)

    # 只读操作 → 放行
    if not is_write_tool(tool_name, tool_args):
        return PolicyResult(allowed=True)

    # 写入操作 → 检查路径白名单
    file_path = tool_args.get("file_path", "")
    if policy.path_filter is not None and file_path and policy.path_filter(file_path):
        return PolicyResult(allowed=True)

    # bash 写入命令
    if tool_name == "bash":
        cmd = tool_args.get("command", "")
        preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return PolicyResult(
            allowed=False,
            reason=f"{policy.label} 禁止执行写入命令: {preview}",
        )

    # 文件写入
    if file_path:
        return PolicyResult(
            allowed=False,
            reason=f"{policy.label} 禁止写入: {file_path}",
        )

    # 其他写入工具（如 cron 写入操作）
    return PolicyResult(
        allowed=False,
        reason=f"{policy.label} 禁止 {tool_name} 写入操作",
    )


# ── 子 Agent 工具过滤 ──


def filter_tools_for_mode(
    tools: list[StructuredTool], policy: ModePolicy
) -> list[StructuredTool]:
    """根据模式策略过滤工具列表

    移除写入工具。bash 保留（其只读性在运行时动态判断）。
    有 path_filter 的策略保留文件写入工具（运行时检查路径）。

    Args:
        tools: 原始工具列表
        policy: 当前模式策略

    Returns:
        过滤后的工具列表
    """
    if policy.allow_write:
        return tools

    filtered = []
    for t in tools:
        # bash 保留：其只读性取决于命令内容，运行时动态判断
        if t.name == "bash":
            filtered.append(t)
            continue
        # 只读工具 → 保留
        if not is_write_tool(t.name, {}):
            filtered.append(t)
            continue
        # 有 path_filter 的写入工具 → 保留（运行时检查路径）
        if policy.path_filter is not None:
            filtered.append(t)
            continue
        # 其余写入工具 → 移除
    return filtered

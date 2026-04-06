"""执行模式策略 — 通用的模式级工具限制体系

三层工具限制机制的 Layer 2：基于当前执行模式的策略评估。

每种执行模式（plan / readonly / 自定义）对应一个 ModePolicy 实例，声明：
- allowed_effects: 无条件允许的 ToolEffect 集合
- path_filter: FILE_WRITE 效果时的路径过滤（返回 True 表示允许写入该路径）
- label: 拒绝消息中显示的模式名称

"normal" 模式无策略限制（policy 为 None），工具调用直接走后续权限引擎。

扩展方式：调用 register_policy("my_mode", ModePolicy(...)) 注册自定义模式。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lumi.agents.tools.capability import ToolEffect, get_tool_effect

if TYPE_CHECKING:
    from langchain_core.tools.structured import StructuredTool


# ── 数据模型 ──


@dataclass(frozen=True)
class ModePolicy:
    """执行模式的工具限制策略

    Attributes:
        name: 模式标识符，如 "plan", "readonly"
        label: 显示名称，用于拒绝消息（如 "Plan mode"）
        allowed_effects: 无条件放行的 ToolEffect 集合
        path_filter: FILE_WRITE 路径白名单函数。
            None → 不允许任何文件写入。
            返回 True → 该路径允许写入。
    """

    name: str
    label: str
    allowed_effects: ToolEffect
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
    allowed_effects=ToolEffect.NONE | ToolEffect.INTERRUPT | ToolEffect.STATE_MUTATE,
    path_filter=_is_under_lumi_plans,
)

READONLY_POLICY = ModePolicy(
    name="readonly",
    label="Read-only mode",
    allowed_effects=ToolEffect.NONE | ToolEffect.INTERRUPT,
    path_filter=None,  # 不允许任何文件写入
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


# ── Layer 2: 通用策略守卫 ──


def check_policy(policy: ModePolicy, tool_name: str, tool_args: dict) -> PolicyResult:
    """检查工具调用是否被模式策略允许

    Args:
        policy: 当前模式的策略
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        PolicyResult，allowed=False 时 reason 说明拒绝原因
    """
    effect = get_tool_effect(tool_name, tool_args)

    # 效果全部在允许集合内 → 放行
    if not (effect & ~policy.allowed_effects):
        return PolicyResult(allowed=True)

    # 文件写入 → 检查路径白名单
    if ToolEffect.FILE_WRITE in effect:
        file_path = tool_args.get("file_path", "")
        if policy.path_filter is not None and policy.path_filter(file_path):
            return PolicyResult(allowed=True)
        return PolicyResult(
            allowed=False,
            reason=f"{policy.label} 禁止写入: {file_path}",
        )

    # Shell 执行（bash 命令未通过只读检查）
    if ToolEffect.SHELL_EXEC in effect:
        cmd = tool_args.get("command", "")
        preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
        return PolicyResult(
            allowed=False,
            reason=f"{policy.label} 禁止执行写入命令: {preview}",
        )

    # 未知效果 → 保守拒绝
    return PolicyResult(
        allowed=False,
        reason=f"{policy.label} 下不允许 {tool_name} 工具",
    )


# ── Layer 3: 子 Agent 工具过滤 ──


def filter_tools_for_mode(
    tools: list["StructuredTool"], policy: ModePolicy
) -> list["StructuredTool"]:
    """根据模式策略过滤工具列表

    移除静态效果不在 allowed_effects 内的工具。
    bash 工具保留（其只读性在运行时由 Layer 2 动态判断）。

    Args:
        tools: 原始工具列表
        policy: 当前模式策略

    Returns:
        过滤后的工具列表
    """
    filtered = []
    for t in tools:
        # bash 保留：其只读性取决于命令内容，运行时由 Layer 2 动态判断
        if t.name == "bash":
            filtered.append(t)
            continue
        effect = get_tool_effect(t.name, {})
        # 静态效果在允许集合内 → 保留
        if not (effect & ~policy.allowed_effects):
            filtered.append(t)
            continue
        # FILE_WRITE 且有 path_filter → 保留（运行时由 Layer 2 检查路径）
        if ToolEffect.FILE_WRITE in effect and policy.path_filter is not None:
            filtered.append(t)
            continue
        # 其余 → 移除
    return filtered

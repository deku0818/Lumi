"""权限审批富化：在 Bridge 层为 tool_approval 请求补充权限评估与边界检查，
使 Graph 侧保持纯净的三态契约。"""

from __future__ import annotations

from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.permissions.matcher import COMMAND_ARG_KEYS, extract_arg
from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.permissions.validators import validate_bash_command
from lumi.utils.logger import logger


def enrich_tool_approval(engine: PermissionEngine, data: dict) -> dict:
    """补充 ``decisions`` / ``warnings`` / ``boundary_violations``（后两者非空才带）。"""
    engine.reload()

    decisions: list[str] = []
    warnings: list[str] = []
    boundary_violations: list[str] = []

    for tc in data.get("tool_calls", []):
        name = tc.get("name", "")
        args = tc.get("args", {})

        try:
            boundary_violations.extend(engine.get_boundary_violations(name, args))
        except Exception as e:
            logger.error("[Bridge] 边界检查异常 (%s): %s", name, e, exc_info=True)
            warnings.append(f"⚠ 工具 {name} 边界检查失败，无法确认是否超出工作区")

        try:
            decision = engine.evaluate(name, args)
        except Exception as e:
            logger.error("[Bridge] 权限评估异常 (%s): %s", name, e, exc_info=True)
            decision = PermissionDecision.UNMATCHED
        decisions.append(decision.value)
        if decision == PermissionDecision.DENY:
            warnings.append(f"⚠ 工具 {name} 命中 deny 规则，该操作被标记为危险")

        if name == "bash":
            for w in validate_bash_command(extract_arg(args, COMMAND_ARG_KEYS) or ""):
                prefix = "⚠" if w.level == "danger" else "⚡"
                warnings.append(f"{prefix} {w.message}")

    data["decisions"] = decisions
    if warnings:
        data["warnings"] = warnings
    if boundary_violations:
        data["boundary_violations"] = boundary_violations
    return data

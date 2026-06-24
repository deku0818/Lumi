"""权限审批富化（从 AgentBridge 拆出的职责子模块）。

在 Bridge 层为 tool_approval 中断补充权限评估、边界检查与选项，
使 Graph 侧保持纯净的三态契约。逻辑逐字照搬自原 AgentBridge。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.gateway.bridge.core import AgentBridge


class ApprovalEnricher:
    """tool_approval 中断数据的 Bridge 层富化。"""

    def __init__(self, bridge: AgentBridge) -> None:
        self._bridge = bridge

    def enrich_tool_approval(self, data: dict) -> dict:
        """在 Bridge 层为 tool_approval 中断数据补充权限评估信息。

        原 human_approval 节点中的权限评估、边界检查、选项构建逻辑迁移至此，
        使 Graph 侧保持纯净的三态契约。
        """
        from lumi.agents.permissions.matcher import (
            build_exact_expr,
        )
        from lumi.agents.permissions.models import PermissionDecision
        from lumi.agents.permissions.validators import validate_bash_command

        engine = (
            self._bridge._context.permission_engine if self._bridge._context else None
        )
        tool_calls = data.get("tool_calls", [])

        if engine is None:
            # 无权限引擎：返回默认选项
            data["options"] = [
                {"key": "approve", "label": "允许本次执行"},
                {"key": "reject", "label": "拒绝"},
            ]
            return data

        engine.reload()

        decisions: list[str] = []
        warnings: list[str] = []
        boundary_violations: list[str] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})

            # 工作区边界检查
            try:
                violations = engine.get_boundary_violations(name, args)
                boundary_violations.extend(violations)
            except Exception as e:
                logger.error("[Bridge] 边界检查异常 (%s): %s", name, e, exc_info=True)
                warnings.append(f"⚠ 工具 {name} 边界检查失败，无法确认是否超出工作区")

            # 权限评估
            try:
                decision = engine.evaluate(name, args)
            except Exception as e:
                logger.error("[Bridge] 权限评估异常 (%s): %s", name, e, exc_info=True)
                decision = PermissionDecision.UNMATCHED
            decisions.append(decision.value)
            if decision == PermissionDecision.DENY:
                warnings.append(f"⚠ 工具 {name} 命中 deny 规则，该操作被标记为危险")
            elif decision == PermissionDecision.ASK:
                warnings.append(f"ℹ 工具 {name} 命中 ask 规则，需要确认")

            # Bash 安全校验器警告
            if name == "bash":
                cmd = args.get("command") or args.get("cmd", "")
                for w in validate_bash_command(cmd):
                    prefix = "⚠" if w.level == "danger" else "⚡"
                    warnings.append(f"{prefix} {w.message}")

        # 构造审批选项
        options: list[dict] = []
        has_deny = any(d == "deny" for d in decisions)
        needs_permission_options = any(
            d in ("deny", "unmatched", "ask") for d in decisions
        ) or bool(boundary_violations)

        if has_deny:
            # DENY 命中：防御性分支（正常流程 DENY 不到达此处）
            options = [{"key": "reject", "label": "拒绝（命中 deny 规则）"}]
        elif needs_permission_options and tool_calls:
            from lumi.agents.tools.capability import is_file_edit_tool

            tc = tool_calls[0]
            exact_expr = build_exact_expr(tc.get("name", ""), tc.get("args", {}))

            options = [
                {"key": "allow_once", "label": "允许执行这一次"},
                {
                    "key": "always_allow_exact",
                    "label": f"始终允许: {exact_expr}",
                    "tool_expr": exact_expr,
                },
            ]
            if all(is_file_edit_tool(t.get("name", "")) for t in tool_calls):
                options.append(
                    {"key": "accept_edits_session", "label": "本次会话自动编辑"}
                )
            options.append({"key": "reject", "label": "拒绝"})

        # 丰富 interrupt 数据
        data["decisions"] = decisions
        if options:
            data["options"] = options
        if warnings:
            data["warnings"] = warnings
        if boundary_violations:
            data["boundary_violations"] = boundary_violations

        return data

    def add_allow_rule(self, tool_expr: str) -> None:
        """持久化 allow 规则到权限引擎"""
        b = self._bridge
        if b._context and b._context.permission_engine:
            b._context.permission_engine.add_allow_rule(tool_expr)
        else:
            logger.warning(
                "[Bridge] add_allow_rule 跳过: 权限引擎不可用 (expr=%s)", tool_expr
            )

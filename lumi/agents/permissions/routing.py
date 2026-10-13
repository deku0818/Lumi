"""按权限规则与审批模式独立判定一个工具调用。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.tools.capability import is_file_edit_tool
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.agents.permissions.engine import PermissionEngine


def route_decision(
    tool_call: dict,
    tool_mode: str,
    engine: PermissionEngine | None,
) -> Literal["ToolExecutor", "HumanApproval", "AutoClassify", "Reject"]:
    """deny 总是拒绝，allow 总是放行；ask / 未匹配按模式裁决。

    privileged 直接放行，auto 交 AI，default 交人工。
    accept_edits 仅对未匹配规则的 write/edit 自动放行。
    """
    try:
        decision = (
            engine.evaluate(tool_call["name"], tool_call.get("args", {}))
            if engine is not None
            else PermissionDecision.UNMATCHED
        )
    except Exception:
        logger.exception(
            "[PermissionCheck] 工具 %s 权限评估失败，拒绝该调用", tool_call["name"]
        )
        return "Reject"
    if decision == PermissionDecision.DENY:
        return "Reject"
    if decision == PermissionDecision.ALLOW or tool_mode == "privileged":
        return "ToolExecutor"
    if tool_mode == "auto":
        return "AutoClassify"
    if (
        tool_mode == "accept_edits"
        and decision == PermissionDecision.UNMATCHED
        and is_file_edit_tool(tool_call["name"])
    ):
        return "ToolExecutor"
    return "HumanApproval"

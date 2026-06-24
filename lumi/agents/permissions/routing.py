"""is_use_tool 的路由决策纯函数。

把工具调用批次（tool_calls 非空后）的全部分支逻辑从 core.nodes.is_use_tool 下沉
到此处，使核心节点变薄壳，路由语义集中在 permissions 层。

依赖方向：permissions → tools.capability（合法，无环），不 import core。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lumi.agents.core.structured_tool import is_internal_tool
from lumi.agents.permissions.mode_policy import check_policy, get_policy
from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.permissions.safety import is_bypass_immune
from lumi.agents.tools.capability import is_file_edit_tool, is_write_tool
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.agents.permissions.engine import PermissionEngine


def route_decision(
    tool_calls: list[dict],
    tool_mode: str,
    execution_mode: str,
    engine: PermissionEngine | None,
) -> str:
    """对一批非空 tool_calls 计算下一节点名。

    路由优先级（与 is_use_tool 文档一致）：
    2. 纯内部伪工具 → ToolExecutor（绕过权限审批）；混合批次落到正常评估
    6. 权限引擎 DENY（优先于只读短路与 bypass）→ HumanApproval
    5/6. 只读工具批次 → ToolExecutor
    4. 执行模式策略守卫 → PolicyReject
    8. bypass-immune（所有模式）→ HumanApproval
    7. accept_edits 模式 → 文件编辑工具工作区内放行，其余 HumanApproval
    9/10. 权限引擎完整评估：
        - privileged → ASK 审批，其余放行
        - auto → ALLOW 放行，其余交 AutoClassify（AI 分类器裁决）
        - default → 全 ALLOW 放行，否则 HumanApproval
    """
    if any(is_internal_tool(tc.get("name", "")) for tc in tool_calls):
        if all(is_internal_tool(tc.get("name", "")) for tc in tool_calls):
            # 纯内部伪工具：安全的框架控制流，闭包内自校验，绕过权限审批快速进 ToolExecutor
            return "ToolExecutor"
        # 混合批次（内部工具 + 其他工具）：不绕过审批，落到下方正常权限评估，
        # 兄弟工具该 DENY/ASK/审批的照常处理，内部工具随批一起评估（read-only，无害）
        logger.warning("[is_use_tool] 内部工具与其他工具混合调用，按正常权限评估整批")

    # 权限引擎 DENY 检查（优先于 bypass，deny 规则不可绕过）
    if engine is not None:
        engine.reload()
        for tc in tool_calls:
            try:
                decision = engine.evaluate(tc["name"], tc.get("args", {}))
                if decision == PermissionDecision.DENY:
                    return "HumanApproval"
            except Exception as e:
                logger.error(
                    "[PermissionCheck] DENY 前置检查异常 (%s): %s, 保守要求审批",
                    tc["name"],
                    e,
                    exc_info=True,
                )
                # fail-closed：评估抛错时直接审批，否则该工具可能被下方只读短路
                # 跳过完整评估而绕过它本应命中的 DENY 规则
                return "HumanApproval"

    # 只读工具跳过审批，直接执行（内部伪工具是安全框架控制流，视为只读参与短路）
    if all(
        is_internal_tool(tc.get("name", ""))
        or not is_write_tool(tc.get("name", ""), tc.get("args", {}))
        for tc in tool_calls
    ):
        return "ToolExecutor"

    # 执行模式策略守卫（Layer 2: 根据当前模式策略拦截不允许的工具调用）
    if execution_mode != "normal":
        policy = get_policy(execution_mode)
        if policy is not None:
            for tc in tool_calls:
                result = check_policy(policy, tc.get("name", ""), tc.get("args", {}))
                if not result.allowed:
                    logger.info(
                        "[PolicyGuard] %s 拒绝: %s - %s",
                        policy.label,
                        tc.get("name"),
                        result.reason,
                    )
                    return "PolicyReject"

    # bypass-immune 安全检查（所有模式都执行）
    for tc in tool_calls:
        args = tc.get("args", {})
        try:
            immune, reason = is_bypass_immune(tc["name"], args)
        except Exception as e:
            logger.error(
                "[SafetyCheck] bypass-immune 检查异常 (%s): %s, 保守要求审批",
                tc["name"],
                e,
                exc_info=True,
            )
            return "HumanApproval"
        if immune:
            logger.warning("[SafetyCheck] Bypass-immune: %s", reason)
            return "HumanApproval"

    # accept_edits 模式：文件编辑工具(write/edit)在工作区内自动放行
    if tool_mode == "accept_edits":
        all_auto = True
        for tc in tool_calls:
            name = tc.get("name", "")
            if is_file_edit_tool(name):
                if engine is not None and engine.check_workspace_boundary(
                    name, tc.get("args", {})
                ):
                    continue
                all_auto = False
                break
            else:
                all_auto = False
                break
        if all_auto:
            return "ToolExecutor"
        return "HumanApproval"

    # 权限引擎完整评估（deny 已在上方处理，此处处理 allow/ask/unmatched）

    if engine is not None:
        # 引擎已在上方 DENY 预检处 reload；同一同步调用内 mtime 不变，无需再次 reload
        has_deny = False
        has_ask = False
        all_allowed = True

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            try:
                decision = engine.evaluate(name, args)
                boundary_ok = engine.check_workspace_boundary(name, args)
                logger.debug(
                    "[PermissionCheck] 工具 %s: decision=%s, boundary_ok=%s",
                    name,
                    decision.value,
                    boundary_ok,
                )
                if decision == PermissionDecision.DENY:
                    has_deny = True
                    break
                if decision == PermissionDecision.ASK:
                    has_ask = True
                if decision != PermissionDecision.ALLOW or not boundary_ok:
                    all_allowed = False
            except Exception as e:
                logger.error(
                    "[PermissionCheck] 工具 %s 权限评估异常: %s, 保守要求审批",
                    name,
                    e,
                    exc_info=True,
                )
                # 评估异常时保守处理：所有模式都要求人工审批
                return "HumanApproval"

        # DENY：所有模式下路由到审批节点（节点内自动拒绝）
        if has_deny:
            return "HumanApproval"

        # privileged 模式：ASK 仍需审批，其余自动放行
        if tool_mode == "privileged":
            if has_ask:
                return "HumanApproval"
            return "ToolExecutor"

        # auto 模式：engine 已显式 ALLOW + 边界 OK 直接放行（用户明示信任，
        # 等价 CC 的 rule-allow 先于分类器）；其余本该问人的批次交分类器裁决。
        # DENY 与 bypass-immune 已在上方短路到 HumanApproval，对 auto 同样免疫。
        if tool_mode == "auto":
            if all_allowed:
                return "ToolExecutor"
            return "AutoClassify"

        # default 模式：全部 ALLOW + 边界 OK 才直接执行
        if all_allowed:
            return "ToolExecutor"

        return "HumanApproval"

    # engine is None：privileged 放行，auto 交分类器，default/accept_edits 审批
    if tool_mode == "privileged":
        logger.warning("[is_use_tool] 权限引擎不可用，privileged 模式直接放行")
        return "ToolExecutor"
    if tool_mode == "auto":
        logger.warning("[is_use_tool] 权限引擎不可用，auto 模式交分类器裁决")
        return "AutoClassify"
    logger.warning("[is_use_tool] 权限引擎不可用，回退到人工审批")
    return "HumanApproval"

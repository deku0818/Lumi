"""LumiApp approval handling — tool approval, ask dialog, plan approval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lumi.tui.agent_bridge import BridgeEvent
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.plan_approval import PlanApproval
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from textual.widget import Widget

    from lumi.tui.app import LumiApp
    from lumi.tui.widgets.chat_log import ChatLog


# ── Approval anchor & visibility helpers ───────────────────────────


def get_approval_anchor(app: LumiApp) -> Widget:
    """Return the widget before which approval dialogs are mounted."""
    from lumi.tui.widgets.input_bar import InputBar

    return app.query_one(InputBar)


def hide_input_for_approval(app: LumiApp) -> None:
    """Hide input bar during approval — the approval widget takes over."""
    from lumi.tui.widgets.input_bar import InputBar

    app.query_one(InputBar).display = False


def restore_input_after_approval(app: LumiApp) -> None:
    """Restore input bar after approval completes."""
    from lumi.tui.widgets.input_bar import InputBar

    app.query_one(InputBar).display = True


def set_tool_waiting(app: LumiApp, tool_call_id: str, fallback_name: str) -> None:
    """Find a ToolBlock and set it to WAITING status."""
    key = tool_call_id or fallback_name
    block = app._assembler.tool_blocks.get(key)
    if block is None:
        result = app._assembler.find_tool_block_by_name(fallback_name)
        if result is not None:
            _, block = result
    if block:
        block.set_waiting()


# ── Event handlers (mounted from EventRouter via AppCallbacks) ─────


async def handle_ask(app: LumiApp, evt: BridgeEvent, chat_log: ChatLog) -> None:
    """Handle ASK event: mount AskDialog."""
    set_tool_waiting(app, (evt.data or {}).get("tool_call_id", ""), "ask")
    dialog = AskDialog(evt.data)
    hide_input_for_approval(app)
    await app.mount(dialog, before=get_approval_anchor(app))


async def handle_tool_approval(
    app: LumiApp, evt: BridgeEvent, chat_log: ChatLog
) -> None:
    """Handle TOOL_APPROVAL event: mount ToolApproval widget."""
    app._assembler.finalize_assistant_msg()
    app._run.last_approval_data = dict(evt.data or {})
    tool_calls = (evt.data or {}).get("tool_calls", [])
    app._run.last_approval_tool_calls = tool_calls

    for tc in tool_calls:
        key = tc.get("id") or tc.get("name", "unknown")
        app._assembler.pop_tool_block(key)

    approval = ToolApproval(evt.data)

    if evt.parent_run_id:
        app._subagent_tracker.set_approval_context(evt.parent_run_id)
    else:
        app._subagent_tracker.clear_approval_context()

    app._hide_todos_bar_for_approval()
    hide_input_for_approval(app)
    await app.mount(approval, before=get_approval_anchor(app))


async def handle_exit_plan_mode(
    app: LumiApp, evt: BridgeEvent, chat_log: ChatLog
) -> None:
    """Handle EXIT_PLAN_MODE event: mount PlanApproval widget."""
    set_tool_waiting(app, (evt.data or {}).get("tool_call_id", ""), "ExitPlanMode")
    dialog = PlanApproval(evt.data)
    hide_input_for_approval(app)
    await app.mount(dialog, before=get_approval_anchor(app))


# ── Decision handlers (Textual message events) ────────────────────


async def on_ask_answered(app: LumiApp, answer: str) -> None:
    """Process AskDialog answer."""
    from lumi.agents.tools.providers.ask import ASK_CANCELLED

    if answer == ASK_CANCELLED:
        from lumi.tui.widgets.chat_log import ChatLog

        chat_log = app.query_one(ChatLog)
        block = app._assembler.tool_blocks.get("ask")
        if block:
            block.set_error("User declined to answer questions")
        await chat_log.auto_scroll_if_needed()

    restore_input_after_approval(app)
    await app._run_resume(answer)


async def on_tool_approval_decided(app: LumiApp, decision: str) -> None:
    """Process ToolApproval decision."""
    from lumi.agents.tools.permissions.workspace import add_authorized_directory

    approval_data = app._run.last_approval_data

    if decision in ("always_allow_exact", "always_allow_pattern"):
        _persist_allow_rule(app, decision, approval_data)
        resume_value: dict = {"decision": "approve"}
    elif decision in ("approve", "allow_once"):
        for v in approval_data.get("boundary_violations", []):
            add_authorized_directory(v)
        resume_value = {"decision": "approve"}
    elif decision == "cancel":
        resume_value = {
            "decision": "cancel",
            "message": "用户中断了工具调用请求",
        }
        await _show_rejection_tool_blocks(app, "用户中断了审批")
    else:
        reason = _build_reject_reason(approval_data)
        resume_value = {"decision": "reject", "message": reason}
        await _show_rejection_tool_blocks(app, "用户拒绝了此工具执行")

    app._restore_todos_bar_after_approval()
    restore_input_after_approval(app)
    app._subagent_tracker.clear_approval_context()
    await app._run_resume(resume_value)


async def on_plan_approval_decided(app: LumiApp, decision: str) -> None:
    """Process PlanApproval decision."""
    from lumi.agents.tools.providers.plan import PLAN_REJECTED
    from lumi.tui.widgets.input_bar import InputBar

    restore_input_after_approval(app)
    if decision == "rejected":
        await app._run_resume(PLAN_REJECTED)
    else:
        app.query_one(InputBar).set_plan_mode(False)
        await app._run_resume(decision)


# ── Internal helpers ───────────────────────────────────────────────


def _persist_allow_rule(app: LumiApp, decision_key: str, approval_data: dict) -> None:
    """Extract tool_expr from approval data and persist to permission engine."""
    options = approval_data.get("options", [])
    expr = next(
        (o["tool_expr"] for o in options if o.get("key") == decision_key),
        None,
    )
    if expr:
        app._bridge.add_allow_rule(expr)
    else:
        logger.error(
            "[ToolApproval] 无法找到 tool_expr (decision=%s, options=%s)，规则未持久化",
            decision_key,
            [o.get("key") for o in options],
        )
    for v in approval_data.get("boundary_violations", []):
        app._bridge.add_workspace(v)


def _build_reject_reason(approval_data: dict) -> str:
    """Build a human-readable rejection reason from approval data."""
    warnings = approval_data.get("warnings", [])
    if warnings:
        return "用户拒绝了工具执行: " + "; ".join(warnings)
    return "用户拒绝了工具执行"


async def _show_rejection_tool_blocks(app: LumiApp, error_msg: str) -> None:
    """Create error-marked ToolBlocks for rejected/cancelled tool calls."""
    from lumi.tui.widgets.chat_log import ChatLog

    chat_log = app.query_one(ChatLog)
    tool_calls = app._run.last_approval_tool_calls

    agent_block = app._subagent_tracker.get_approval_block()
    is_agent_group_mode = (
        agent_block is not None and app._assembler.agent_group is not None
    )
    if not is_agent_group_mode:
        for tc in tool_calls:
            name = tc.get("name", "unknown")
            args = tc.get("args", {})
            block = ToolBlock(name, args)
            await chat_log.mount(block)
            block.set_error(error_msg)
    await chat_log.auto_scroll_if_needed()

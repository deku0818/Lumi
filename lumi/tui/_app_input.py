"""LumiApp input handling — submission, streaming, cancel, ctrl+c, scroll."""

from __future__ import annotations

import asyncio
import time as _time
from typing import TYPE_CHECKING

from textual.css.query import NoMatches

from lumi.tui.event_router import EventRouter
from lumi.tui.run_state import RunPhase
from lumi.tui.theme import get_color
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.tool_block import ToolStatus
from lumi.utils.clipboard import copy_to_clipboard
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.tui.app import LumiApp
    from lumi.tui.widgets.chat_log import ChatLog

# Interval (seconds) within which double-press is detected
_DOUBLE_ESC_INTERVAL: float = 0.5
_DOUBLE_CTRL_C_INTERVAL: float = 1.5
_MAX_ERROR_LENGTH: int = 300


# ── Submission ─────────────────────────────────────────────────────


def prepend_text_block(content: str | list, text: str) -> list:
    """Inject a text block at the front of content, converting str to list if needed."""
    block = {"type": "text", "text": text}
    if isinstance(content, list):
        return [block, *content]
    return [block, {"type": "text", "text": content}]


async def handle_submission(app: LumiApp, event) -> None:
    """Process an InputBar.Submitted event."""
    from lumi.tui.slash_commands.models import CommandType
    from lumi.tui.slash_commands.parser import parse_command_input
    from lumi.tui.widgets.chat_log import ChatLog
    from lumi.tui.widgets.input_bar import InputBar
    from lumi.tui.widgets.user_message import UserMessage

    if app._run.is_running:
        return

    await _try_dismiss_command_panel(app)

    text = event.text
    tool_mode = event.tool_mode
    plan_mode = event.plan_mode
    plan_reminder_pending = event.plan_reminder_pending
    images = event.images
    chat_log = app.query_one(ChatLog)

    await chat_log.mount(UserMessage(text, image_count=len(images)))
    await chat_log.auto_scroll_if_needed()

    # Slash command routing
    if text.startswith("/"):
        command_name, extra_text = parse_command_input(text)
        command = app._command_registry.get(command_name)
        if command:
            try:
                await command.handler(extra_text)
                if command.command_type == CommandType.BUILTIN:
                    app._pending_system_commands.append(f"/{command_name}")
            except Exception as e:
                logger.error("[LumiApp] 命令 /%s 执行失败", command_name, exc_info=True)
                await chat_log.append_error(f"/{command_name} 执行失败:", str(e))
            return

    # Build multimodal content blocks if images present
    if images:
        content: str | list = [{"type": "text", "text": text}]
        for img in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.data}"},
                }
            )
    else:
        content = text

    # Inject interruption notice
    if app._interrupted:
        content = prepend_text_block(
            content,
            "<system-reminder>\n"
            "The user interrupted the conversation before the previous reply was completed.\n"
            "</system-reminder>\n",
        )
        app._interrupted = False

    # Inject pending system commands
    if app._pending_system_commands:
        hints = "".join(
            f"<command-name>{cmd}</command-name><command-type>system</command-type>\n"
            for cmd in app._pending_system_commands
        )
        content = prepend_text_block(content, hints)
        app._pending_system_commands.clear()

    # Plan mode reminder injection
    if plan_mode and plan_reminder_pending:
        from lumi.agents.tools.providers.plan import plan_mode_response

        content = prepend_text_block(content, plan_mode_response)
        app.query_one(InputBar).consume_plan_reminder()

    app._run.phase = RunPhase.IDLE
    app._run.start()
    app.query_one(InputBar).set_disabled(True)

    execution_mode = "plan" if plan_mode else "normal"
    app._run.task = asyncio.create_task(
        run_stream(app, content, tool_mode, execution_mode=execution_mode)
    )


# ── Streaming ──────────────────────────────────────────────────────


async def run_stream(
    app: LumiApp,
    content: str | list,
    tool_mode: str = "auto",
    execution_mode: str = "normal",
) -> None:
    """Execute the streaming response loop."""
    await consume_events(
        app,
        app._bridge.stream_response(content, tool_mode, execution_mode=execution_mode),
    )


async def run_resume(app: LumiApp, value) -> None:
    """Resume the agent after an approval/ask interruption."""
    app._subagent_tracker.prepare_for_resume()
    app._run.task = asyncio.create_task(
        consume_events(app, app._bridge.stream_resume(value))
    )


async def consume_events(app: LumiApp, event_stream) -> None:
    """Consume bridge events and dispatch through the EventRouter."""
    from lumi.tui.widgets.chat_log import ChatLog

    chat_log = app.query_one(ChatLog)
    router = EventRouter(app._run, app._assembler, app._subagent_tracker, app)
    try:
        async for evt in event_stream:
            await router.dispatch(evt, chat_log)
            await chat_log.auto_scroll_if_needed()
            chat_log.schedule_compact()
    except Exception as e:
        logger.error("[TUI] 事件流异常: %s", e, exc_info=True)
        await show_error(app, chat_log, str(e))


async def show_error(app: LumiApp, chat_log: ChatLog, error: str) -> None:
    """Display a truncated error message and finish the run."""
    if len(error) > _MAX_ERROR_LENGTH:
        error = error[:_MAX_ERROR_LENGTH] + "..."
    await chat_log.append_error("Error:", error)
    app._finish_run()


# ── Cancel / Ctrl+C / Key ─────────────────────────────────────────


async def action_cancel_generation(app: LumiApp) -> None:
    """Handle Escape key: dismiss screens, cancel approvals, or cancel generation."""
    from lumi.tui.widgets.ask_dialog import AskDialog
    from lumi.tui.widgets.tool_approval import ToolApproval

    # Dismiss pushed screens (e.g., ResumeScreen, SettingsScreen)
    if len(app.screen_stack) > 1:
        app.screen.dismiss(None)
        return

    if await _try_dismiss_command_panel(app):
        return

    # Active ToolApproval → cancel
    try:
        approval = app.query_one(ToolApproval)
        approval.post_message(ToolApproval.Decided("cancel"))
        approval.call_later(approval.remove)
        return
    except NoMatches:
        pass

    # Active AskDialog → decline
    try:
        dialog = app.query_one(AskDialog)
        dialog._decline()
        return
    except NoMatches:
        pass

    # Running → cancel generation
    if app._run.is_running:
        if app._run.task and not app._run.task.done():
            app._run.task.cancel()
        for block in app._assembler.tool_blocks.values():
            if block.status == ToolStatus.RUNNING:
                block.set_interrupted()
        if app._assembler.agent_group is not None:
            app._assembler.agent_group.force_finalize()
        app._finalize_assistant_msg()
        from lumi.tui.widgets.chat_log import ChatLog

        chat_log = app.query_one(ChatLog)
        await chat_log.append_hint(
            "● ",
            "Interrupted",
            style=f"dim {get_color('error')}",
        )
        app._interrupted = True
        app._finish_run()
        return

    # IDLE: double-Esc → open rewind screen
    now = _time.monotonic()
    if now - app._last_esc < _DOUBLE_ESC_INTERVAL:
        app._last_esc = 0.0
        from lumi.tui._app_screens import open_rewind_screen

        await open_rewind_screen(app)
        return
    app._last_esc = now


async def action_handle_ctrl_c(app: LumiApp) -> None:
    """Ctrl+C: clear input if non-empty; double-tap to quit."""
    from lumi.tui.widgets.input_bar import ChatInput, InputBar

    try:
        inp = app.query_one("#user-input", ChatInput)
    except NoMatches:
        await app.action_quit_app()
        return

    # Non-empty input → clear text
    if inp.value:
        inp.value = ""
        app._last_ctrl_c = 0.0
        return

    # Pending images → clear images
    input_bar = app.query_one(InputBar)
    if input_bar.has_pending_images:
        input_bar.clear_images()
        app._last_ctrl_c = 0.0
        return

    # Double-tap detection
    now = _time.monotonic()
    if now - app._last_ctrl_c < _DOUBLE_CTRL_C_INTERVAL:
        await app.action_quit_app()
        return
    app._last_ctrl_c = now
    input_bar.show_exit_hint()


def action_scroll_chat(app: LumiApp, direction: str) -> None:
    """Scroll the chat log or approval content area."""
    from lumi.tui.widgets.plan_approval import PlanApproval
    from lumi.tui.widgets.tool_approval import ToolApproval

    approval = app._query_safe(ToolApproval) or app._query_safe(PlanApproval)
    if approval is not None:
        approval.scroll_content(direction)
        return

    from lumi.tui.widgets.chat_log import ChatLog

    chat_log = app._query_safe(ChatLog)
    if chat_log is None:
        return

    if direction == "up":
        chat_log.scroll_up(animate=False)
    elif direction == "down":
        chat_log.scroll_down(animate=False)
    elif direction == "page_up":
        chat_log.scroll_page_up(animate=False)
    elif direction == "page_down":
        chat_log.scroll_page_down(animate=False)


# ── Copy ───────────────────────────────────────────────────────────


def get_last_assistant_raw(app: LumiApp) -> str | None:
    """Get the raw text of the most recent AssistantMessage."""
    try:
        from lumi.tui.widgets.chat_log import ChatLog

        chat_log = app.query_one(ChatLog)
        msgs = chat_log.query(AssistantMessage)
        if msgs:
            last: AssistantMessage = msgs.last()
            return last._raw if last._has_content else None
    except NoMatches:
        pass
    return None


async def action_copy_selection(app: LumiApp) -> None:
    """Copy selected text to clipboard, falling back to last AI reply."""
    from lumi.tui.widgets.input_bar import InputBar

    text = app.screen.get_selected_text()
    if not text:
        text = get_last_assistant_raw(app)
    if not text:
        return
    ok = await asyncio.to_thread(copy_to_clipboard, text)
    try:
        app.query_one(InputBar).flash_message("Copied" if ok else "Copy failed")
    except NoMatches:
        pass


# ── Command panel ──────────────────────────────────────────────────


async def _try_dismiss_command_panel(app: LumiApp) -> bool:
    """Dismiss command result panel if visible. Returns True if dismissed."""
    from lumi.tui.widgets.command_result_panel import CommandResultPanel
    from lumi.tui.widgets.chat_log import ChatLog

    panel = app.query_one(CommandResultPanel)
    if panel.is_visible:
        panel.hide()
        chat_log = app.query_one(ChatLog)
        await chat_log.append_hint("└ ", "对话框已关闭")
        return True
    return False

"""LumiApp screen navigation — resume, rewind, skills, agents, cron, mcp, settings, clear."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from lumi.tui.theme import get_color
from lumi.tui.widgets.chat_log import ChatLog
from lumi.utils.config import GlobalConfig, GlobalConfigManager, get_config
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id

if TYPE_CHECKING:
    from lumi.tui.app import LumiApp


# ── Generic list-screen launcher ───────────────────────────────────


async def push_list_screen(
    app: LumiApp,
    items: Sequence,
    screen_factory: Callable[[Sequence], object],
    empty_hint: str,
    callback: Callable | None = None,
) -> None:
    """Standard pattern: show hint if empty, otherwise push a list screen."""
    if not items:
        await app.query_one(ChatLog).append_hint("● ", empty_hint)
        return
    app.push_screen(screen_factory(items), callback=callback or (lambda _: None))


# ── Resume ─────────────────────────────────────────────────────────


async def open_resume_screen(app: LumiApp) -> None:
    """Open the session resume picker."""
    from lumi.tui.session_store import list_sessions

    checkpoint_mode = get_config().config.agents.checkpoint
    if checkpoint_mode == "memory":
        chat_log = app.query_one(ChatLog)
        await chat_log.append_hint(
            "● ",
            "当前 checkpoint 模式为 memory，会话不会持久化。"
            "请在 config.yaml 中设置 agents.checkpoint: sqlite 以启用会话恢复。",
        )
        return

    graph = app._bridge.graph
    if graph is None:
        await app.query_one(ChatLog).append_hint("● ", "Agent 未初始化，无法恢复会话")
        return

    sessions = await list_sessions(
        graph,
        current_thread_id=app._bridge.current_thread_id,
        workspace=app._workspace_dir,
    )
    if not sessions:
        await app.query_one(ChatLog).append_hint("● ", "没有可恢复的历史会话")
        return

    from lumi.tui.screens.resume_screen import ResumeScreen

    app.push_screen(
        ResumeScreen(sessions), callback=lambda tid: _on_resume_done(app, tid)
    )


async def _on_resume_done(app: LumiApp, thread_id: str | None) -> None:
    """Resume selection callback: switch thread and re-render history."""
    if thread_id is None:
        return

    from lumi.tui.message_restore import restore_messages
    from lumi.tui._app_lifecycle import mount_title_block

    app._bridge.switch_thread(thread_id)

    chat_log = app.query_one(ChatLog)
    await chat_log.remove_children()
    await mount_title_block(app, chat_log)

    todos = await restore_messages(app._bridge.graph, chat_log, thread_id)
    if todos:
        app._update_todos_bar(todos)
    else:
        app._clear_todos_bar()

    await chat_log.scroll_to_end()
    app._reset_session_state()


# ── Clear conversation ─────────────────────────────────────────────


async def clear_conversation(app: LumiApp) -> None:
    """Clear current conversation and start a new session."""
    from lumi.tui._app_lifecycle import mount_title_block

    new_tid = generate_thread_id()
    app._bridge.switch_thread(new_tid)

    chat_log = app.query_one(ChatLog)
    await chat_log.remove_children()
    await mount_title_block(app, chat_log)
    await chat_log.append_hint("● ", "已开始新会话")
    await chat_log.scroll_to_end()

    app._clear_todos_bar()
    app._reset_session_state()


# ── Rewind ─────────────────────────────────────────────────────────


async def open_rewind_screen(app: LumiApp) -> None:
    """Open the rewind checkpoint picker."""
    checkpoints = await app._bridge.list_checkpoints()
    if not checkpoints:
        await app.query_one(ChatLog).append_hint("● ", "No checkpoints available")
        return

    app._rewind_checkpoints = {cp.commit_hash: cp for cp in checkpoints}

    from lumi.tui.screens.rewind_screen import RewindScreen

    app.push_screen(
        RewindScreen(checkpoints, initial_index=-1),
        callback=lambda h: _on_rewind_done(app, h),
    )


async def _on_rewind_done(app: LumiApp, commit_hash: str | None) -> None:
    """Rewind selection callback: restore files and conversation state."""
    if commit_hash is None:
        app._rewind_checkpoints = {}
        return

    from lumi.tui._app_lifecycle import mount_title_block
    from lumi.tui.message_restore import restore_messages
    from lumi.tui.widgets.input_bar import ChatInput

    target = app._rewind_checkpoints.get(commit_hash)
    app._rewind_checkpoints = {}

    if target is None:
        await app.query_one(ChatLog).append_hint("● ", "Checkpoint not found")
        return

    success, warning = await app._bridge.rewind_to_checkpoint(target)

    chat_log = app.query_one(ChatLog)
    if not success:
        await chat_log.append_hint(
            "● ",
            f"Rewind failed: {warning}",
            style=f"dim {get_color('error')}",
        )
        return

    await chat_log.remove_children()
    await mount_title_block(app, chat_log)

    thread_id = app._bridge.current_thread_id
    app._clear_todos_bar()
    if target.langgraph_checkpoint_id:
        todos = await restore_messages(
            app._bridge.graph,
            chat_log,
            thread_id,
            checkpoint_id=target.langgraph_checkpoint_id,
        )
        if todos:
            app._update_todos_bar(todos)

    if warning:
        await chat_log.append_hint(
            "● ",
            warning,
            style=f"dim {get_color('warning')}",
        )

    await chat_log.scroll_to_end()

    # Fill input box with the rewound prompt
    from textual.css.query import NoMatches

    try:
        inp = app.query_one("#user-input", ChatInput)
        inp.value = target.label
        inp.move_cursor(inp.document.end)
    except NoMatches:
        logger.debug("[LumiApp] rewind 后未找到输入框组件")

    app._reset_session_state()


# ── Skills ─────────────────────────────────────────────────────────


async def open_skills_screen(app: LumiApp) -> None:
    """Open the skills list screen."""
    from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector

    detector = SkillChangeDetector.get_instance()
    skills, _ = detector.check()

    from lumi.tui.screens.skills_screen import SkillsScreen

    await push_list_screen(app, skills, SkillsScreen, "暂无可用技能")


# ── Agents ─────────────────────────────────────────────────────────


async def open_agents_screen(app: LumiApp) -> None:
    """Open the agents list screen."""
    from lumi.agents.tools.loader import load_agents

    agents = load_agents()

    from lumi.tui.screens.agents_screen import AgentsScreen

    await push_list_screen(app, agents, AgentsScreen, "暂无可用 Agent")


# ── Cron management ────────────────────────────────────────────────


async def open_cron_screen(app: LumiApp) -> None:
    """Open the cron jobs management screen."""
    if app._scheduler is None:
        await app.query_one(ChatLog).append_hint(
            "● ", "定时任务子系统未启动，/cron 不可用"
        )
        return

    jobs = await app._scheduler.get_all_jobs()

    from lumi.tui.screens.cron_screen import CronScreen

    app.push_screen(
        CronScreen(jobs, on_delete=lambda jid: _delete_cron_job(app, jid)),
        callback=lambda r: _on_cron_done(app, r),
    )


async def _delete_cron_job(app: LumiApp, job_id: str) -> None:
    """Execute cron job deletion."""
    if app._scheduler is None:
        logger.warning("[LumiApp] 无法删除任务 %s: 调度器未初始化", job_id)
        return
    job = await app._scheduler.get_job(job_id)
    name = job.name if job else job_id
    await app._scheduler.delete_job(job_id)
    await app.query_one(ChatLog).append_hint(
        "● ", f"已删除定时任务「{name}」({job_id})"
    )


async def _on_cron_done(app: LumiApp, result: str | None) -> None:
    """Cron screen close callback."""
    if result == "changed":
        app._refresh_bell()


# ── Cron notifications ─────────────────────────────────────────────


async def open_cron_notify_screen(app: LumiApp) -> None:
    """Open the cron notifications screen."""
    from lumi.tui.widgets.notification_panel import NotificationStore

    store = NotificationStore(app._cron_notifications_path)
    records = store.load()

    from lumi.tui.screens.cron_notify_screen import CronNotifyScreen

    await push_list_screen(
        app,
        records,
        lambda r: CronNotifyScreen(r, store=store),
        "暂无通知",
        lambda r: _on_cron_notify_done(app, r),
    )


async def _on_cron_notify_done(app: LumiApp, result: str | None) -> None:
    """Notification screen close callback."""
    if result == "changed":
        app._refresh_bell()


# ── MCP ────────────────────────────────────────────────────────────


async def open_mcp_screen(app: LumiApp) -> None:
    """Open the MCP server status screen."""
    from lumi.agents.tools.providers.mcp import get_mcp_session_manager

    manager = get_mcp_session_manager()
    servers = manager.get_server_info()

    from lumi.tui.screens.mcp_screen import MCPScreen

    await push_list_screen(app, servers, MCPScreen, "未配置任何 MCP 服务器")


# ── Settings ───────────────────────────────────────────────────────


async def open_settings(app: LumiApp) -> None:
    """Open the settings screen."""
    from lumi.tui.screens.settings_screen import SettingsScreen

    if app._global_config is None:
        app._global_config = GlobalConfigManager.load()
    app.push_screen(
        SettingsScreen(app._global_config),
        callback=lambda result: _on_settings_done(app, result),
    )


async def _on_settings_done(app: LumiApp, result: GlobalConfig | None) -> None:
    """Settings screen close callback."""
    if result is not None:
        from lumi.tui._app_lifecycle import apply_theme_mode

        app._global_config = result
        await apply_theme_mode(app, result.theme_mode)

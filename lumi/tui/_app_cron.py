"""LumiApp cron and notification helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from lumi.tui.run_state import RunPhase
from lumi.utils.constants import MAX_NOTIFICATIONS
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.tui.app import LumiApp


def on_cron_job_status(app: LumiApp, job_names: list[str]) -> None:
    """Scheduler callback: update InputBar cron indicator."""
    from lumi.tui.widgets.input_bar import InputBar
    from textual.css.query import NoMatches

    try:
        app.query_one(InputBar).update_cron_status(job_names)
    except NoMatches:
        logger.debug("[LumiApp] _on_cron_job_status: InputBar 未挂载，跳过")


def refresh_bell(app: LumiApp) -> None:
    """Reload notification store and update the bell unread count."""
    from textual.css.query import NoMatches

    from lumi.tui.widgets.input_bar import InputBar

    try:
        from lumi.tui.widgets.notification_panel import NotificationStore

        records = NotificationStore(app._cron_notifications_path).load()
        unread = sum(1 for r in records if not r.read)
    except Exception:
        logger.warning("[LumiApp] 通知记录加载失败", exc_info=True)
        return

    try:
        app.query_one(InputBar).update_bell(unread)
    except NoMatches:
        logger.debug("[LumiApp] InputBar 尚未挂载，跳过铃铛更新")
    except Exception:
        logger.warning("[LumiApp] 铃铛更新失败, unread=%s", unread, exc_info=True)


def add_notification(
    app: LumiApp,
    job_name: str,
    output: str,
    started_at: datetime | None = None,
    duration_ms: int | None = None,
) -> None:
    """Persist a cron notification record and refresh the bell."""
    try:
        from lumi.tui.widgets.notification_panel import (
            NotificationRecord,
            NotificationStore,
        )

        store = NotificationStore(app._cron_notifications_path)
        records = store.load()
        record = NotificationRecord.create(
            job_name, output, started_at=started_at, duration_ms=duration_ms
        )
        records.insert(0, record)
        if len(records) > MAX_NOTIFICATIONS:
            records = records[:MAX_NOTIFICATIONS]
        store.save(records)
    except Exception:
        logger.warning("[LumiApp] 保存通知失败: job=%s", job_name, exc_info=True)
        return
    refresh_bell(app)


async def poll_notifications(app: LumiApp) -> None:
    """Poll background notification queue.

    Only processes notifications when the agent is idle.
    Drains the queue and sends combined content as a user message.
    """
    from textual.css.query import NoMatches

    from lumi.tui.widgets.input_bar import InputBar

    if app._run.is_running:
        return

    notifications = app._bridge.drain_notifications()
    if not notifications:
        return

    logger.info("[LumiApp] 收到 %d 条后台任务通知", len(notifications))
    combined = "\n".join(notifications)
    hint = f"{combined}\nRead the output file to retrieve the result."

    app._run.phase = RunPhase.THINKING
    app._run.start()
    try:
        app.query_one(InputBar).set_disabled(True)
    except NoMatches:
        logger.error("[LumiApp] 通知处理时找不到 InputBar，跳过")
        app._run.phase = RunPhase.IDLE
        return
    app._run.task = asyncio.create_task(app._run_stream(hint, tool_mode="auto"))

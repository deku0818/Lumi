"""LumiApp lifecycle helpers — init, mount, theme detection, shutdown."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from lumi import __version__
from lumi.agents.cron.delivery import DeliveryManager, TUIDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.tui.widgets.title_block import TitleBlock
from lumi.utils.config import GlobalConfig, get_config
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from lumi.tui.app import LumiApp
    from lumi.tui.widgets.chat_log import ChatLog


# ── Theme detection ────────────────────────────────────────────────


async def detect_system_theme() -> bool:
    """Detect OS theme preference. Returns True for dark mode."""
    if sys.platform == "darwin":
        return await _detect_macos_theme()
    if sys.platform == "win32":
        return _detect_windows_theme()
    return True


async def _detect_macos_theme() -> bool:
    """macOS dark theme detection via `defaults` command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "defaults",
            "read",
            "-g",
            "AppleInterfaceStyle",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        return stdout.decode().strip().lower() == "dark"
    except asyncio.TimeoutError:
        logger.debug("[LumiApp] 系统主题检测超时，使用暗色主题")
        return True
    except FileNotFoundError:
        logger.debug("[LumiApp] 'defaults' 命令不可用，使用暗色主题")
        return True
    except OSError:
        logger.warning("[LumiApp] 系统主题检测意外失败，使用暗色主题", exc_info=True)
        return True


def _detect_windows_theme() -> bool:
    """Windows dark theme detection via registry."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        logger.debug("[LumiApp] Windows 注册表主题检测失败，使用暗色主题")
        return True


async def apply_theme_mode(app: LumiApp, mode: str) -> None:
    """Apply theme mode: 'dark', 'light', or 'system'."""
    if mode == "dark":
        app.theme = "lumi-dark"
    elif mode == "light":
        app.theme = "lumi-light"
    else:
        is_dark = await detect_system_theme()
        logger.info("系统主题检测结果: dark=%s", is_dark)
        app.theme = "lumi-dark" if is_dark else "lumi-light"


# ── Mount helpers ──────────────────────────────────────────────────


async def mount_title_block(
    app: LumiApp, chat_log: ChatLog, recent_sessions: list | None = None
) -> None:
    """Create and mount a TitleBlock into the chat log."""
    title = TitleBlock(
        model_name=app._bridge.model_name,
        recent_sessions=recent_sessions or [],
        id="title-block",
    )
    title.border_title = f"Lumi v{__version__}"
    await chat_log.mount(title)


async def load_recent_sessions(app: LumiApp, limit: int = 3) -> list:
    """Load recent session summaries from checkpointer.

    Returns an empty list on failure.
    """
    graph = app._bridge.graph
    if graph is None or graph.checkpointer is None:
        return []
    try:
        from lumi.tui.session_store import list_sessions

        return await list_sessions(
            graph,
            current_thread_id=app._bridge.current_thread_id,
            workspace=app._workspace_dir,
            limit=limit,
        )
    except Exception:
        logger.debug("[LumiApp] 加载最近会话失败", exc_info=True)
        return []


# ── Cron subsystem init ────────────────────────────────────────────


async def init_cron_subsystem(
    app: LumiApp,
) -> tuple[Scheduler | None, DeliveryManager | None]:
    """Initialize the cron scheduler and delivery manager.

    Returns (scheduler, delivery) on success, (None, None) on failure.
    """
    try:
        cron_dir = app._cron_dir
        cron_dir.mkdir(parents=True, exist_ok=True)
        _write_workspace_meta(cron_dir, app._workspace_dir)

        job_store = JobStore(cron_dir / "jobs.json")
        run_log = RunLog(cron_dir / "runs")
        delivery = DeliveryManager()
        delivery.register(TUIDelivery(app))

        scheduler = Scheduler(
            job_store,
            run_log,
            delivery,
            on_job_status=app._on_cron_job_status,
        )
        init_cron_tool(scheduler, job_store, run_log)
        await scheduler.start()

        logger.info("[LumiApp] 定时任务子系统已启动 (workspace=%s)", app._workspace_id)
        return scheduler, delivery
    except Exception:
        logger.warning("[LumiApp] 定时任务子系统启动失败", exc_info=True)
        app.notify("定时任务子系统启动失败，cron 功能不可用", severity="warning")
        return None, None


def _write_workspace_meta(cron_dir: Path, workspace_dir: str) -> None:
    """Write workspace.meta file for debugging (non-critical)."""
    try:
        meta_file = cron_dir / "workspace.meta"
        if not meta_file.exists():
            meta_file.write_text(
                json.dumps(
                    {
                        "path": workspace_dir,
                        "created_at": datetime.now().isoformat(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    except OSError:
        logger.debug("workspace.meta 写入失败（非关键）", exc_info=True)


# ── Finish mount (full initialization) ─────────────────────────────


async def finish_mount(app: LumiApp) -> None:
    """Complete app initialization after init-flow or direct mount."""
    import os

    from lumi.tui.widgets.chat_log import ChatLog
    from lumi.tui.widgets.input_bar import InputBar
    from lumi.tui.widgets.run_status_bar import RunStatusBar
    from lumi.tui.widgets.status_line import StatusLine

    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        logger.info(
            "检测到 SSH 环境。如遇鼠标问题，请使用 --no-mouse 标志或设置 LUMI_NO_MOUSE=1"
        )

    app._assembler = WidgetAssembler(app.query_one(ChatLog))
    app.query_one(RunStatusBar).bind_run_context(app._run)

    await apply_theme_mode(app, app._global_config.theme_mode)

    # Inject env vars from config.yaml
    try:
        get_config().apply_env()
    except Exception as e:
        logger.warning("注入环境变量失败: %s", e)

    # Initialize agent bridge
    try:
        await app._bridge.initialize()
    except Exception as e:
        chat_log = app.query_one(ChatLog)
        await chat_log.append_error("初始化失败:", str(e))
        return

    app._bridge.init_checkpoint(Path.cwd())

    # Configure StatusLine with model context length
    from lumi.utils.model_info import fetch_model_info
    from lumi.utils.read_config import get_config as get_yaml_config

    config_context_length = get_yaml_config().config.token.context_length
    model_name = app._bridge.model_name

    model_info = await fetch_model_info(model_name)
    context_max = (
        model_info.context_length
        if model_info and model_info.context_length > 0
        else config_context_length
    )

    app.query_one(StatusLine).configure(
        run_ctx=app._run,
        model_name=model_name,
        context_max=context_max,
    )

    chat_log = app.query_one(ChatLog)
    recent_sessions = await load_recent_sessions(app)
    await mount_title_block(app, chat_log, recent_sessions=recent_sessions)

    # Slash commands
    app._init_slash_commands()
    app._refresh_bell()

    # Cron subsystem
    app._scheduler, app._delivery = await init_cron_subsystem(app)

    # Start notification polling
    from lumi.tui.app import _NOTIFICATION_POLL_INTERVAL

    app._notification_poll_timer = app.set_interval(
        _NOTIFICATION_POLL_INTERVAL, app._poll_notifications
    )

    # Custom keybindings
    bind_custom_keys(app, app._global_config)

    # Privileged mode
    if app._privileged:
        app.query_one(InputBar).set_privileged(True)


def bind_custom_keys(app: LumiApp, config: GlobalConfig) -> None:
    """Bind user-configured keyboard shortcuts."""
    kb = config.keybindings
    try:
        app.bind(kb.copy_selection, "copy_selection", description="复制选中文本")
    except Exception:
        logger.warning(
            "[LumiApp] 绑定快捷键失败: copy_selection=%s",
            kb.copy_selection,
            exc_info=True,
        )


# ── Shutdown ───────────────────────────────────────────────────────


async def shutdown(app: LumiApp) -> None:
    """Gracefully close scheduler, delivery manager, and bridge."""
    try:
        if app._scheduler:
            await app._scheduler.stop()
        if app._delivery:
            await app._delivery.close_all()
        await app._bridge.close()
    except Exception:
        logger.warning("[LumiApp] 关闭资源时出错", exc_info=True)

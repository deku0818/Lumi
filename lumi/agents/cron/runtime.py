"""Cron 运行时装配：TUI 与 desktop serve 共用的初始化工厂。

按工作目录隔离组装 JobStore / RunLog / Scheduler，并注入 cron 工具依赖。
调用方负责注册投递通道和在事件循环中启停 scheduler。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger
from lumi.utils.workspace_id import get_workspace_dir, get_workspace_id


@dataclass(frozen=True)
class CronRuntime:
    """组装完成的 cron 子系统句柄。"""

    scheduler: Scheduler
    job_store: JobStore
    run_log: RunLog
    delivery: DeliveryManager
    cron_dir: Path


def setup_cron(
    delivery: DeliveryManager,
    on_job_status: Callable[[list[str]], None] | None = None,
) -> CronRuntime:
    """组装当前 workspace 的 cron 子系统并注入 cron 工具依赖。

    不启动调度器——调用方在合适的时机 ``await runtime.scheduler.start()``。

    Args:
        delivery: 已注册好投递通道的 DeliveryManager。
        on_job_status: 任务开始/结束时的运行状态回调。

    Returns:
        组装完成的 CronRuntime。
    """
    cron_dir = GLOBAL_CONFIG_DIR / "cron" / get_workspace_id()
    cron_dir.mkdir(parents=True, exist_ok=True)
    _write_workspace_meta(cron_dir)

    job_store = JobStore(cron_dir / "jobs.json")
    run_log = RunLog(cron_dir / "runs")
    scheduler = Scheduler(
        job_store,
        run_log,
        delivery,
        on_job_status=on_job_status,
        lock_path=cron_dir / "scheduler.lock",
    )
    init_cron_tool(scheduler, job_store, run_log)
    return CronRuntime(scheduler, job_store, run_log, delivery, cron_dir)


def _write_workspace_meta(cron_dir: Path) -> None:
    """写入 workspace.meta 便于调试（非关键，失败不影响 cron）。"""
    meta_file = cron_dir / "workspace.meta"
    if meta_file.exists():
        return
    try:
        meta_file.write_text(
            json.dumps(
                {
                    "path": get_workspace_dir(),
                    "created_at": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.debug("workspace.meta 写入失败（非关键）", exc_info=True)

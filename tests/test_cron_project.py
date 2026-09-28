"""定时任务绑定所属项目：建任务时记下会话项目，执行时在该项目里跑。

此前 Job 不记项目、执行一律 ``initialize(project_dir="")`` 退回 serve 进程 cwd：在 A 项目
会话里建的任务，跑起来的权限边界 / MCP / 项目说明都不是 A 的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, Schedule, ScheduleType
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.permissions.workspace import set_run_authorized_source
from lumi.agents.tools.providers.cron import cron, init_cron_tool


@pytest.fixture
def scheduler(tmp_path: Path) -> Scheduler:
    store = JobStore(tmp_path / "jobs.json")
    run_log = RunLog(tmp_path / "runs")
    s = Scheduler(job_store=store, run_log=run_log, delivery=DeliveryManager())
    init_cron_tool(s, store, run_log)
    return s


def test_job_dict_roundtrip_keeps_project():
    job = Job(
        name="n",
        schedule=Schedule(type=ScheduleType.INTERVAL, value="5m"),
        prompt="p",
        project_dir="/proj",
    )
    assert Job.from_dict(job.to_dict()).project_dir == "/proj"
    legacy = job.to_dict()
    del legacy["project_dir"]  # 存量 jobs.json 没有该字段
    assert Job.from_dict(legacy).project_dir == ""


async def test_cron_tool_records_session_project(scheduler: Scheduler, tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    set_run_authorized_source(lambda: [project])
    try:
        await cron.ainvoke(
            {
                "operation": "create",
                "name": "日报",
                "schedule": "1d",
                "prompt": "写日报",
            }
        )
    finally:
        set_run_authorized_source(None)
    [job] = await scheduler._job_store.load()
    assert job.project_dir == str(project)


async def test_scheduler_runs_job_in_its_project(scheduler: Scheduler):
    seen: list[str] = []

    async def runner(prompt: str, thread_id: str, project_dir: str) -> str:
        seen.append(project_dir)
        return "ok"

    scheduler.set_stream_runner(runner)
    job = Job(
        name="n",
        schedule=Schedule(type=ScheduleType.INTERVAL, value="5m"),
        prompt="p",
        project_dir="/proj",
    )
    record = await scheduler._execute_job(job)
    assert record.status == "success"
    assert seen == ["/proj"]

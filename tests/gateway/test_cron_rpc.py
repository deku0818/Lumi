"""cron RPC 方法测试：纯 CRUD 与序列化断言，不启动调度器、不执行真实任务。"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import rpc

from lumi.agents.cron.delivery import DeliveryManager
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.agents.cron.runtime import CronRuntime
from lumi.agents.cron.scheduler import Scheduler
from lumi.gateway.cron_rpc import set_cron_runtime


@pytest.fixture
def cron_runtime(tmp_path):
    """tmp_path 下组装 CronRuntime 并注入 cron_rpc，调度器不启动。"""
    delivery = DeliveryManager()
    job_store = JobStore(tmp_path / "jobs.json")
    run_log = RunLog(tmp_path / "runs")
    scheduler = Scheduler(job_store, run_log, delivery)
    runtime = CronRuntime(scheduler, job_store, run_log, delivery, tmp_path)
    set_cron_runtime(runtime)
    yield runtime
    set_cron_runtime(None)


async def _create(name: str = "每日总结", schedule: str = "0 9 * * *") -> dict:
    result = await rpc(
        "create_cron_job",
        {"name": name, "schedule": schedule, "prompt": "总结今天的待办"},
    )
    return result["job"]


# -- create / list --


async def test_create_and_list(cron_runtime):
    job = await _create()
    assert job["name"] == "每日总结"
    assert job["schedule"] == {"type": "cron", "value": "0 9 * * *"}
    assert job["enabled"] is True
    assert "next_run" in job

    result = await rpc("list_cron_jobs", {})
    assert [j["id"] for j in result["jobs"]] == [job["id"]]


async def test_create_invalid_schedule_raises(cron_runtime):
    with pytest.raises(ValueError, match="无法识别调度规则"):
        await _create(schedule="every day")


async def test_create_empty_name_raises(cron_runtime):
    with pytest.raises(ValueError, match="不能为空"):
        await rpc("create_cron_job", {"name": " ", "schedule": "5m", "prompt": "x"})


# -- update / toggle / delete --


async def test_update_fields(cron_runtime):
    job = await _create()
    result = await rpc(
        "update_cron_job",
        {"job_id": job["id"], "name": "新名字", "schedule": "10m", "prompt": "新载荷"},
    )
    updated = result["job"]
    assert updated["name"] == "新名字"
    assert updated["schedule"] == {"type": "interval", "value": "10m"}
    assert updated["prompt"] == "新载荷"

    # 持久化生效
    stored = await cron_runtime.job_store.get(job["id"])
    assert stored.name == "新名字"


async def test_update_unknown_job_raises(cron_runtime):
    with pytest.raises(ValueError, match="不存在"):
        await rpc("update_cron_job", {"job_id": "nope", "name": "x"})


async def test_update_empty_name_raises(cron_runtime):
    """显式传空串应报错（与 create 校验一致），而非静默忽略。"""
    job = await _create()
    with pytest.raises(ValueError, match="不能为空"):
        await rpc("update_cron_job", {"job_id": job["id"], "name": " "})
    with pytest.raises(ValueError, match="不能为空"):
        await rpc("update_cron_job", {"job_id": job["id"], "schedule": ""})


async def test_toggle_disables_and_persists(cron_runtime):
    job = await _create()
    result = await rpc("toggle_cron_job", {"job_id": job["id"], "enabled": False})
    assert result["job"]["enabled"] is False
    stored = await cron_runtime.job_store.get(job["id"])
    assert stored.enabled is False


async def test_delete_removes_job(cron_runtime):
    job = await _create()
    result = await rpc("delete_cron_job", {"job_id": job["id"]})
    assert result["job_id"] == job["id"]
    assert (await rpc("list_cron_jobs", {}))["jobs"] == []


async def test_delete_unknown_job_raises(cron_runtime):
    with pytest.raises(ValueError, match="不存在"):
        await rpc("delete_cron_job", {"job_id": "nope"})


async def test_run_unknown_job_raises(cron_runtime):
    with pytest.raises(ValueError, match="不存在"):
        await rpc("run_cron_job", {"job_id": "nope"})


# -- runs --


async def test_list_cron_runs(cron_runtime):
    job = await _create()
    record = RunRecord(
        job_id=job["id"],
        job_name=job["name"],
        started_at=datetime(2026, 6, 10, 9, 0, 0),
        finished_at=datetime(2026, 6, 10, 9, 0, 5),
        status="success",
        duration_ms=5000,
        output_summary="完成",
        thread_id="cron-abc123",
    )
    await cron_runtime.run_log.append(record)

    result = await rpc("list_cron_runs", {"job_id": job["id"]})
    assert len(result["runs"]) == 1
    assert result["runs"][0]["status"] == "success"
    assert result["runs"][0]["output_summary"] == "完成"
    assert result["runs"][0]["thread_id"] == "cron-abc123"


async def test_list_jobs_carries_run_threads(cron_runtime):
    """任务 wire 带回近期可跳转的 run（前端未读角标的唯一数据源），无会话的 run 不计入。"""
    job = await _create()
    for i, tid in enumerate(["cron-a", "", "cron-b"]):
        await cron_runtime.run_log.append(
            RunRecord(
                job_id=job["id"],
                job_name=job["name"],
                started_at=datetime(2026, 6, 10, 9, i, 0),
                finished_at=datetime(2026, 6, 10, 9, i, 5),
                status="success",
                duration_ms=5000,
                output_summary="ok",
                thread_id=tid,
            )
        )

    jobs = (await rpc("list_cron_jobs", {}))["jobs"]
    # 时间倒序，空 thread_id（无 checkpointer / 已被保留策略清理）不可跳转故剔除
    assert jobs[0]["run_threads"] == ["cron-b", "cron-a"]


async def test_run_record_thread_id_backward_compat():
    """旧格式记录（无 thread_id 字段）反序列化为默认空串。"""
    record = RunRecord.from_dict(
        {
            "job_id": "j1",
            "job_name": "n",
            "started_at": "2026-06-10T09:00:00",
            "finished_at": "2026-06-10T09:00:05",
            "status": "success",
            "duration_ms": 5000,
            "output_summary": "ok",
        }
    )
    assert record.thread_id == ""


# -- 未初始化 --


async def test_uninitialized_raises():
    set_cron_runtime(None)
    with pytest.raises(RuntimeError, match="未启动"):
        await rpc("list_cron_jobs", {})

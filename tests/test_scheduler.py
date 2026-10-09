"""Scheduler 核心功能单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lumi.agents.cron.delivery import DeliveryManager, ResultDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, Schedule, ScheduleType
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.sessions.thread_runs import thread_runs


@pytest.fixture
def job_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.json")


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "runs")


@pytest.fixture
def delivery() -> DeliveryManager:
    return DeliveryManager()


class FakeRunner:
    """注入的执行 runner 替身：返回预设输出 / 抛预设异常 / 可拖慢，并记录调用。"""

    def __init__(self) -> None:
        self.output = "测试输出"
        self.error: BaseException | None = None
        self.delay = 0.0
        self.calls: list[tuple[str, str, str]] = []
        # 进入 runner 即置位：需要「执行中」时点的测试据此同步
        self.started = asyncio.Event()

    async def __call__(self, prompt: str, thread_id: str, project_dir: str) -> str:
        self.calls.append((prompt, thread_id, project_dir))
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.output


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def scheduler(
    job_store: JobStore, run_log: RunLog, delivery: DeliveryManager, runner: FakeRunner
) -> Scheduler:
    return Scheduler(
        job_store=job_store, run_log=run_log, delivery=delivery, stream_runner=runner
    )


async def test_retention_delete_waits_for_active_thread_writeback(scheduler):
    tid = "cron-retention-shared"
    started = asyncio.Event()
    log = []
    checkpointer = AsyncMock()

    async def remove(thread_id):
        assert log == ["writeback"]
        log.append("delete")

    checkpointer.adelete_thread.side_effect = remove
    scheduler._checkpointer = checkpointer

    async def resumed_turn():
        async with thread_runs.lock_for(tid):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                log.append("writeback")

    active = asyncio.create_task(resumed_turn())
    await started.wait()
    await asyncio.wait_for(scheduler._delete_thread(tid), 1)
    with pytest.raises(asyncio.CancelledError):
        await active
    assert log == ["writeback", "delete"]


def _make_interval_job(name: str = "test", interval: str = "5m") -> Job:
    """创建一个 interval 类型的测试任务。"""
    return Job(
        name=name,
        schedule=Schedule(type=ScheduleType.INTERVAL, value=interval),
        prompt=f"执行 {name}",
    )


def _make_cron_job(name: str = "cron-test", expr: str = "*/5 * * * *") -> Job:
    """创建一个 cron 类型的测试任务。"""
    return Job(
        name=name,
        schedule=Schedule(type=ScheduleType.CRON, value=expr),
        prompt=f"执行 {name}",
    )


def _make_at_job(name: str = "once") -> Job:
    """创建一个 at 类型的测试任务。"""
    future = (datetime.now() + timedelta(hours=1)).isoformat()
    return Job(
        name=name,
        schedule=Schedule(type=ScheduleType.AT, value=future),
        prompt=f"执行 {name}",
    )


async def test_start_loads_and_registers_enabled_jobs(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """start() 应从 JobStore 加载启用的任务并注册到 APScheduler。"""
    job_a = _make_interval_job("a")
    job_b = _make_interval_job("b")
    disabled = _make_interval_job("disabled")
    disabled.enabled = False

    await job_store.save([job_a, job_b, disabled])
    await scheduler.start()

    try:
        # 只有启用的任务被注册
        aps_job_ids = {j.id for j in scheduler._aps.get_jobs()}
        assert job_a.id in aps_job_ids
        assert job_b.id in aps_job_ids
        assert disabled.id not in aps_job_ids
    finally:
        await scheduler.stop()


async def test_start_empty_store(scheduler: Scheduler) -> None:
    """空 JobStore 时 start() 应正常启动，不注册任何任务。"""
    await scheduler.start()
    try:
        assert scheduler._aps.get_jobs() == []
    finally:
        await scheduler.stop()


async def test_stop_clears_running_tasks(scheduler: Scheduler) -> None:
    """stop() 应清空 _running_tasks 集合。"""
    await scheduler.start()
    await scheduler.stop()
    assert len(scheduler._running_tasks) == 0


async def test_add_job_registers_to_aps(scheduler: Scheduler) -> None:
    """add_job() 应将任务注册到 APScheduler。"""
    await scheduler.start()
    try:
        job = _make_interval_job()
        scheduler.add_job(job)
        aps_job_ids = {j.id for j in scheduler._aps.get_jobs()}
        assert job.id in aps_job_ids
    finally:
        await scheduler.stop()


async def test_remove_job_from_aps(scheduler: Scheduler) -> None:
    """remove_job() 应从 APScheduler 移除任务。"""
    await scheduler.start()
    try:
        job = _make_interval_job()
        scheduler.add_job(job)
        assert any(j.id == job.id for j in scheduler._aps.get_jobs())

        scheduler.remove_job(job.id)
        assert not any(j.id == job.id for j in scheduler._aps.get_jobs())
    finally:
        await scheduler.stop()


async def test_pause_and_resume_job(scheduler: Scheduler) -> None:
    """pause_job() 应暂停任务，resume_job() 应恢复。"""
    await scheduler.start()
    try:
        job = _make_interval_job()
        scheduler.add_job(job)

        scheduler.pause_job(job.id)
        aps_job = scheduler._aps.get_job(job.id)
        assert aps_job is not None
        assert aps_job.next_run_time is None  # 暂停后 next_run_time 为 None

        scheduler.resume_job(job.id)
        aps_job = scheduler._aps.get_job(job.id)
        assert aps_job is not None
        assert aps_job.next_run_time is not None  # 恢复后有下次运行时间
    finally:
        await scheduler.stop()


async def test_add_job_replace_existing(scheduler: Scheduler) -> None:
    """add_job() 对同 ID 任务应替换而非重复注册。"""
    await scheduler.start()
    try:
        job = _make_interval_job("original", "5m")
        scheduler.add_job(job)

        # 修改调度规则后重新添加
        updated = Job(
            id=job.id,
            name="updated",
            schedule=Schedule(type=ScheduleType.INTERVAL, value="10m"),
            prompt="更新后",
        )
        scheduler.add_job(updated)

        # 应该只有一个同 ID 的任务
        matching = [j for j in scheduler._aps.get_jobs() if j.id == job.id]
        assert len(matching) == 1
    finally:
        await scheduler.stop()


async def test_register_different_trigger_types(scheduler: Scheduler) -> None:
    """_register_job() 应正确处理 interval、cron、at 三种 trigger 类型。"""
    await scheduler.start()
    try:
        interval_job = _make_interval_job()
        cron_job = _make_cron_job()
        at_job = _make_at_job()

        scheduler.add_job(interval_job)
        scheduler.add_job(cron_job)
        scheduler.add_job(at_job)

        aps_job_ids = {j.id for j in scheduler._aps.get_jobs()}
        assert interval_job.id in aps_job_ids
        assert cron_job.id in aps_job_ids
        assert at_job.id in aps_job_ids
    finally:
        await scheduler.stop()


async def test_late_fire_still_runs(scheduler: Scheduler) -> None:
    """休眠或事件循环卡顿后晚到的触发照常执行一次：APScheduler 默认只容忍晚 1 秒，
    超出即静默跳过（一次性任务就此永不执行）。"""
    from apscheduler.triggers.date import DateTrigger

    await scheduler.start()
    fired = asyncio.Event()

    async def fire() -> None:
        fired.set()

    try:
        late = datetime.now() - timedelta(seconds=30)
        scheduler._aps.add_job(fire, trigger=DateTrigger(run_date=late))
        await asyncio.wait_for(fired.wait(), timeout=2)
    finally:
        await scheduler.stop()


async def test_offline_missed_at_job_runs_once_at_start(
    scheduler: Scheduler, job_store: JobStore, runner: FakeRunner
) -> None:
    """离线期间过点的一次性任务只由启动补偿执行一次：再交给 APScheduler 会在启动
    瞬间再触发一次（晚到不限时），快速失败时同一任务被执行两遍。"""
    runner.error = RuntimeError("boom")
    decide = scheduler._should_compensate

    async def slow_decide(job: Job, now: datetime) -> bool:
        await asyncio.sleep(0.3)  # 补偿判定晚于 APScheduler 首轮触发（竞态的坏时序）
        return await decide(job, now)

    scheduler._should_compensate = slow_decide  # type: ignore[method-assign]
    past = (datetime.now() - timedelta(hours=2)).isoformat()
    await job_store.upsert(
        Job(
            name="once", schedule=Schedule(type=ScheduleType.AT, value=past), prompt="p"
        )
    )
    await scheduler.start()
    try:
        await asyncio.sleep(1)
    finally:
        await scheduler.stop()
    assert len(runner.calls) == 1


# --- 任务执行逻辑测试（7.2）---


async def test_execute_job_runs_prompt_and_returns_success(
    scheduler: Scheduler, runner: FakeRunner
) -> None:
    """_execute_job() 经 runner 在独立 cron- thread 里跑任务 prompt，返回 success 的 RunRecord。"""
    job = _make_interval_job("exec-test")
    runner.output = "Agent 执行完成"

    record = await scheduler._execute_job(job)

    assert record.job_id == job.id
    assert record.job_name == job.name
    assert record.status == "success"
    assert "Agent 执行完成" in record.output_summary
    assert record.error == ""
    assert record.duration_ms >= 0
    # thread_id 恒由调度器生成（cron- 前缀），项目随任务透传给 runner
    assert runner.calls == [(job.prompt, record.thread_id, job.project_dir)]
    assert record.thread_id.startswith("cron-")


async def test_execute_job_timeout(scheduler: Scheduler, runner: FakeRunner) -> None:
    """_execute_job() 超时应返回 timeout 状态。"""
    # 使用极短超时
    scheduler._execution_timeout = 0.01
    runner.delay = 10
    job = _make_interval_job("timeout-test")

    record = await scheduler._execute_job(job)

    assert record.status == "timeout"
    assert "超时" in record.error
    assert record.output_summary == ""


async def test_cancel_job_records_stopped(
    scheduler: Scheduler, run_log: RunLog, runner: FakeRunner
) -> None:
    """用户中断运行中的任务：记为 stopped，且 record 照常出（uncancel 后投递不被打断）。"""
    scheduler._execution_timeout = 10  # 够长，确保是 cancel 而非 timeout
    runner.delay = 10  # 会被 cancel 掐断
    job = _make_interval_job("stop-test")
    await scheduler._job_store.upsert(job)  # 执行中的任务恒在库里（不在 = 已被删）

    await scheduler._run_job_task(job)
    task = scheduler._running_tasks[job.id]
    await runner.started.wait()
    assert scheduler.cancel_job(job.id) is True
    record = await task

    assert record.status == "stopped"
    assert "中断" in record.error
    # 中断落进执行日志（record 照常投递，未被残留取消打断）
    runs = await run_log.get_recent(job.id, limit=1)
    assert runs and runs[0].status == "stopped"
    # 标记已清理，不残留
    assert job.id not in scheduler._user_stopped_jobs


async def test_cancel_job_not_running_returns_false(scheduler: Scheduler) -> None:
    """没有运行中的该任务时，cancel_job 返回 False。"""
    assert scheduler.cancel_job("nonexistent") is False


async def test_run_status_broadcast_carries_thread_id(
    job_store: JobStore, run_log: RunLog, delivery: DeliveryManager
) -> None:
    """runner 的产出即执行结果；运行态广播带该 run 的 cron- thread_id。"""
    captured: list[list[dict]] = []

    async def fake_runner(prompt: str, thread_id: str, project_dir: str) -> str:
        assert thread_id.startswith("cron")
        return f"ran:{prompt}"

    scheduler = Scheduler(
        job_store=job_store,
        run_log=run_log,
        delivery=delivery,
        stream_runner=fake_runner,
        on_job_status=captured.append,
    )
    job = _make_interval_job("stream-test")

    record = await scheduler._execute_job(job)

    assert record.status == "success"
    assert record.output_summary == "ran:执行 stream-test"
    assert record.thread_id.startswith("cron")
    # 起始那次广播带着该 run 的 thread_id（前端据此显示活条目）
    assert any(runs and runs[0]["thread_id"].startswith("cron") for runs in captured)
    # 结束时清空
    assert captured[-1] == []


async def test_run_job_task_skips_concurrent_same_job(
    scheduler: Scheduler, runner: FakeRunner
) -> None:
    """同 job 已在跑时再触发（如 run_cron_job 撞调度）应跳过，不新建并发 task。"""
    runner.delay = 10
    job = _make_interval_job("concurrent")

    await scheduler._run_job_task(job)
    await runner.started.wait()
    assert len(scheduler._running_tasks) == 1

    await scheduler._run_job_task(job)  # 同 job 再触发 → 跳过
    assert len(scheduler._running_tasks) == 1

    for task in list(scheduler._running_tasks.values()):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_execute_job_failure(scheduler: Scheduler, runner: FakeRunner) -> None:
    """runner 抛错（如 cron_stream 检测到 ERROR 事件后补抛）→ 如实记 failed。"""
    job = _make_interval_job("fail-test")
    runner.error = RuntimeError("模拟错误")

    record = await scheduler._execute_job(job)

    assert record.status == "failed"
    assert "RuntimeError" in record.error
    assert "模拟错误" in record.error


async def test_execute_job_records_to_run_log(
    scheduler: Scheduler, run_log: RunLog
) -> None:
    """_execute_job() 应将执行记录写入 RunLog。"""
    job = _make_interval_job("log-test")
    await scheduler._job_store.upsert(job)  # 执行中的任务恒在库里（不在 = 已被删）

    await scheduler._execute_job(job)

    records = await run_log.get_recent(job.id)
    assert len(records) == 1
    assert records[0].job_id == job.id
    assert records[0].status == "success"


async def test_execute_job_broadcasts_result(
    scheduler: Scheduler, runner: FakeRunner
) -> None:
    """_execute_job() 应通过 DeliveryManager 广播结果。"""
    mock_channel = AsyncMock(spec=ResultDelivery)
    scheduler._delivery.register(mock_channel)

    job = _make_interval_job("broadcast-test")
    await scheduler._job_store.upsert(job)  # 执行中的任务恒在库里（不在 = 已被删）
    runner.output = "广播内容"

    await scheduler._execute_job(job)

    mock_channel.deliver.assert_awaited_once()
    record, text = mock_channel.deliver.call_args[0]
    assert record.job_name == job.name
    assert record.status == "success"
    assert "广播内容" in text


async def test_execute_job_at_type_deletes_from_store(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """一次性任务（at 类型）执行后应从 JobStore 删除。"""
    job = _make_at_job("once-delete")
    await job_store.upsert(job)

    await scheduler._execute_job(job)

    # 验证任务已从 JobStore 删除
    remaining = await job_store.get(job.id)
    assert remaining is None


async def test_execute_job_interval_not_deleted(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """周期性任务（interval 类型）执行后不应从 JobStore 删除。"""
    job = _make_interval_job("keep-alive")
    await job_store.upsert(job)

    await scheduler._execute_job(job)

    remaining = await job_store.get(job.id)
    assert remaining is not None


async def test_execute_job_output_truncated_to_500(
    scheduler: Scheduler, runner: FakeRunner
) -> None:
    """_execute_job() 应将输出截取前 500 字符作为 output_summary。"""
    runner.output = "A" * 1000
    job = _make_interval_job("truncate-test")

    record = await scheduler._execute_job(job)

    assert len(record.output_summary) == 500


async def test_run_job_task_adds_to_running_tasks(scheduler: Scheduler) -> None:
    """_run_job_task() 应将 asyncio.Task 加入 _running_tasks 集合。"""
    job = _make_interval_job("task-track")

    await scheduler._run_job_task(job)
    # 给 task 一点时间完成
    await asyncio.sleep(0.1)

    # 任务完成后应自动从集合中移除
    assert len(scheduler._running_tasks) == 0


# --- 失败收尾：cron 层不重试、不回写任务快照 ---


async def test_failed_at_job_is_deleted_without_retry(
    scheduler: Scheduler, job_store: JobStore, runner: FakeRunner
) -> None:
    """一次性任务失败（含超时）同样执行完即删：cron 层不做重试，也不为重试保留任务。"""
    scheduler._execution_timeout = 0.01
    runner.delay = 10
    job = _make_at_job("at-timeout")
    await job_store.upsert(job)

    record = await scheduler._execute_job(job)

    assert record.status == "timeout"
    assert await job_store.get(job.id) is None
    assert scheduler._aps.get_jobs() == []


async def test_failed_run_does_not_roll_back_mid_run_edit(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """执行期间任务被编辑、随后本次失败：收尾不得用执行起点的快照覆盖回去。"""
    job = _make_interval_job("edit-then-fail")
    await job_store.upsert(job)
    edited = Job(
        id=job.id, name=job.name, schedule=job.schedule, prompt="编辑后的 prompt"
    )

    async def runner_edits_then_fails(
        prompt: str, thread_id: str, project_dir: str
    ) -> str:
        await job_store.upsert(edited)
        raise ConnectionError("连接失败")

    scheduler._stream_runner = runner_edits_then_fails
    record = await scheduler._execute_job(job)

    assert record.status == "failed"
    stored = await job_store.get(job.id)
    assert stored is not None and stored.prompt == "编辑后的 prompt"


async def test_failed_run_does_not_resurrect_deleted_job(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """执行期间任务被删、随后本次失败：收尾不得把已删任务写回来。"""
    job = _make_interval_job("delete-then-fail")
    await job_store.upsert(job)

    async def runner_deletes_then_fails(
        prompt: str, thread_id: str, project_dir: str
    ) -> str:
        await job_store.delete(job.id)
        raise ConnectionError("连接失败")

    scheduler._stream_runner = runner_deletes_then_fails
    await scheduler._execute_job(job)

    assert await job_store.get(job.id) is None


# --- trigger() 立即执行测试（7.4）---


async def test_trigger_executes_job_immediately(
    scheduler: Scheduler, job_store: JobStore, runner: FakeRunner
) -> None:
    """trigger() 应立即执行指定任务。"""
    job = _make_interval_job("trigger-test")
    await job_store.upsert(job)

    await scheduler.trigger(job.id)
    # 等待 task 完成
    await asyncio.sleep(0.1)

    # 验证 runner 被调用
    assert [prompt for prompt, _, _ in runner.calls] == [job.prompt]

    # 验证执行记录已写入 RunLog
    records = await scheduler._run_log.get_recent(job.id)
    assert len(records) == 1
    assert records[0].status == "success"


async def test_trigger_raises_key_error_for_unknown_id(
    scheduler: Scheduler,
) -> None:
    """trigger() 对不存在的 job_id 应抛出 KeyError。"""
    with pytest.raises(KeyError, match="不存在"):
        await scheduler.trigger("nonexistent-id")


async def test_trigger_does_not_affect_aps_schedule(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """trigger() 不应影响 APScheduler 中任务的正常调度。"""
    job = _make_interval_job("no-affect-test", "10m")
    await job_store.upsert(job)

    await scheduler.start()
    try:
        # 记录 trigger 前的 APScheduler 状态
        aps_job_before = scheduler._aps.get_job(job.id)
        assert aps_job_before is not None
        next_run_before = aps_job_before.next_run_time

        await scheduler.trigger(job.id)
        await asyncio.sleep(0.1)

        # trigger 后 APScheduler 中的任务状态不变
        aps_job_after = scheduler._aps.get_job(job.id)
        assert aps_job_after is not None
        assert aps_job_after.next_run_time == next_run_before
    finally:
        await scheduler.stop()


async def test_delete_running_job_stops_it_and_leaves_no_log(
    scheduler: Scheduler, job_store: JobStore, run_log: RunLog, runner: FakeRunner
) -> None:
    """删除运行中的任务：先停掉这次执行，跑完也不再把刚清掉的执行日志写回来。"""
    scheduler._execution_timeout = 10
    runner.delay = 10
    job = _make_interval_job("del-running")
    await job_store.upsert(job)

    await scheduler._run_job_task(job)
    task = scheduler._running_tasks[job.id]
    await runner.started.wait()
    await asyncio.wait_for(scheduler.delete_job(job.id), 2)
    assert task.done()
    assert await run_log.get_all(job.id) == []


async def test_job_deleting_itself_mid_run(
    scheduler: Scheduler, job_store: JobStore, run_log: RunLog
) -> None:
    """agent 在任务自己的执行里调 cron delete：不能等自己（死锁），跑完也不留执行日志。"""
    job = _make_interval_job("self-delete")
    await job_store.upsert(job)

    async def runner_that_deletes(prompt: str, thread_id: str, project_dir: str) -> str:
        await asyncio.wait_for(scheduler.delete_job(job.id), 2)
        return "删掉了自己"

    scheduler._stream_runner = runner_that_deletes
    await scheduler._run_job_task(job)
    record = await scheduler._running_tasks[job.id]
    assert record.status == "success"
    assert await job_store.get(job.id) is None
    assert await run_log.get_all(job.id) == []


async def test_at_job_edited_to_interval_mid_run_is_kept(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """一次性任务执行期间被改成周期任务：收尾按最新配置判断，不再按快照删掉它。"""
    job = _make_at_job("at-then-interval")
    await job_store.upsert(job)
    edited = Job(
        id=job.id,
        name=job.name,
        schedule=Schedule(type=ScheduleType.INTERVAL, value="5m"),
        prompt=job.prompt,
    )

    async def runner_while_edited(prompt: str, thread_id: str, project_dir: str) -> str:
        await job_store.upsert(edited)
        return "ok"

    scheduler._stream_runner = runner_while_edited
    await scheduler._execute_job(job)
    assert await job_store.get(job.id) is not None


async def test_stop_mark_does_not_outlive_the_run(scheduler: Scheduler) -> None:
    """停止落在收尾投递阶段时标记也要清掉：否则之后关机宽限期的取消被当成用户停止吞掉。"""
    job = _make_interval_job("late-stop")
    await scheduler._job_store.upsert(job)
    delivering = asyncio.Event()
    release = asyncio.Event()

    async def slow_deliver(*args, **kwargs):
        delivering.set()
        await release.wait()

    scheduler._deliver_and_log = slow_deliver  # type: ignore[method-assign]
    await scheduler._run_job_task(job)
    task = scheduler._running_tasks[job.id]
    await delivering.wait()
    scheduler.cancel_job(job.id)
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert job.id not in scheduler._user_stopped_jobs


async def test_trigger_reports_when_already_running(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """同一任务在跑时再触发会被跳过：如实返回未触发，工具别再回复「已触发执行」。"""
    job = _make_interval_job("busy")
    await job_store.upsert(job)
    gate = asyncio.Event()

    async def gated_runner(prompt: str, thread_id: str, project_dir: str) -> str:
        await gate.wait()
        return "ok"

    scheduler._stream_runner = gated_runner
    assert await scheduler.trigger(job.id) is True
    assert await scheduler.trigger(job.id) is False
    gate.set()
    await scheduler._running_tasks[job.id]

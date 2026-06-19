"""Scheduler 核心功能单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumi.agents.cron.delivery import DeliveryManager, ResultDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.models import Job, Schedule, ScheduleType
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler, _is_transient_error
from lumi.utils.constants import MAX_CRON_RETRIES


@pytest.fixture
def job_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.json")


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "runs")


@pytest.fixture
def delivery() -> DeliveryManager:
    return DeliveryManager()


@pytest.fixture
def scheduler(
    job_store: JobStore, run_log: RunLog, delivery: DeliveryManager
) -> Scheduler:
    return Scheduler(job_store=job_store, run_log=run_log, delivery=delivery)


def _mock_create_agent(output_content: str = "测试输出") -> AsyncMock:
    """创建一个模拟的 create_agent，返回预设输出。"""
    mock_msg = MagicMock()
    mock_msg.content = output_content

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={"messages": [mock_msg]})

    mock_agent = MagicMock()
    mock_agent.graph = mock_graph

    mock_context = MagicMock()

    create_agent_mock = AsyncMock(return_value=(mock_agent, mock_context))
    return create_agent_mock


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


# --- 任务执行逻辑测试（7.2）---

# patch 目标：_execute_job 内部通过 lazy import 引入 create_agent
_PATCH_CREATE_AGENT = "lumi.agents.core.graph.create_agent"


async def test_execute_job_creates_agent_and_returns_success(
    scheduler: Scheduler, run_log: RunLog
) -> None:
    """_execute_job() 应创建独立 Agent 子会话并返回 success 状态的 RunRecord。"""
    job = _make_interval_job("exec-test")
    mock_create = _mock_create_agent("Agent 执行完成")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        record = await scheduler._execute_job(job)

    assert record.job_id == job.id
    assert record.job_name == job.name
    assert record.status == "success"
    assert "Agent 执行完成" in record.output_summary
    assert record.error == ""
    assert record.duration_ms >= 0

    # 验证 create_agent 被正确调用
    mock_create.assert_awaited_once_with(checkpointer=None)


async def test_execute_job_sets_tool_mode_privileged(scheduler: Scheduler) -> None:
    """_execute_job() 应将 tool_mode 设为 'privileged' 跳过人工审批。"""
    job = _make_interval_job("auto-mode-test")
    mock_create = _mock_create_agent()

    with patch(_PATCH_CREATE_AGENT, mock_create):
        await scheduler._execute_job(job)

    # 验证 ainvoke 的 inputs 包含 tool_mode="privileged"
    call_args = mock_create.return_value[0].graph.ainvoke.call_args
    inputs = call_args[0][0]
    assert inputs["tool_mode"] == "privileged"


async def test_execute_job_timeout(scheduler: Scheduler, run_log: RunLog) -> None:
    """_execute_job() 超时应返回 timeout 状态。"""
    # 使用极短超时
    scheduler._execution_timeout = 0.01
    job = _make_interval_job("timeout-test")

    async def slow_invoke(*args, **kwargs):
        await asyncio.sleep(10)
        return {"messages": [MagicMock(content="不应到达")]}

    mock_create = _mock_create_agent()
    mock_create.return_value[0].graph.ainvoke = slow_invoke

    with patch(_PATCH_CREATE_AGENT, mock_create):
        record = await scheduler._execute_job(job)

    assert record.status == "timeout"
    assert "超时" in record.error
    assert record.output_summary == ""


async def test_execute_job_failure(scheduler: Scheduler) -> None:
    """_execute_job() Agent 执行异常应返回 failed 状态。"""
    job = _make_interval_job("fail-test")
    mock_create = _mock_create_agent()
    mock_create.return_value[0].graph.ainvoke = AsyncMock(
        side_effect=RuntimeError("模拟错误")
    )

    with patch(_PATCH_CREATE_AGENT, mock_create):
        record = await scheduler._execute_job(job)

    assert record.status == "failed"
    assert "RuntimeError" in record.error
    assert "模拟错误" in record.error


async def test_execute_job_records_to_run_log(
    scheduler: Scheduler, run_log: RunLog
) -> None:
    """_execute_job() 应将执行记录写入 RunLog。"""
    job = _make_interval_job("log-test")
    mock_create = _mock_create_agent("日志测试输出")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        await scheduler._execute_job(job)

    records = await run_log.get_recent(job.id)
    assert len(records) == 1
    assert records[0].job_id == job.id
    assert records[0].status == "success"


async def test_execute_job_broadcasts_result(scheduler: Scheduler) -> None:
    """_execute_job() 应通过 DeliveryManager 广播结果。"""
    mock_channel = AsyncMock(spec=ResultDelivery)
    scheduler._delivery.register(mock_channel)

    job = _make_interval_job("broadcast-test")
    mock_create = _mock_create_agent("广播内容")

    with patch(_PATCH_CREATE_AGENT, mock_create):
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

    mock_create = _mock_create_agent("一次性任务完成")

    with patch(_PATCH_CREATE_AGENT, mock_create):
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

    mock_create = _mock_create_agent("周期任务输出")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        await scheduler._execute_job(job)

    remaining = await job_store.get(job.id)
    assert remaining is not None


async def test_execute_job_output_truncated_to_500(scheduler: Scheduler) -> None:
    """_execute_job() 应将输出截取前 500 字符作为 output_summary。"""
    long_output = "A" * 1000
    job = _make_interval_job("truncate-test")
    mock_create = _mock_create_agent(long_output)

    with patch(_PATCH_CREATE_AGENT, mock_create):
        record = await scheduler._execute_job(job)

    assert len(record.output_summary) == 500


async def test_run_job_task_adds_to_running_tasks(scheduler: Scheduler) -> None:
    """_run_job_task() 应将 asyncio.Task 加入 _running_tasks 集合。"""
    job = _make_interval_job("task-track")
    mock_create = _mock_create_agent("跟踪测试")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        await scheduler._run_job_task(job)
        # 给 task 一点时间完成
        await asyncio.sleep(0.1)

    # 任务完成后应自动从集合中移除
    assert len(scheduler._running_tasks) == 0


# --- 重试逻辑测试（7.3）---


class TestIsTransientError:
    """_is_transient_error() 瞬态错误判定测试。"""

    def test_timeout_error_is_transient(self) -> None:
        assert _is_transient_error(TimeoutError()) is True

    def test_connection_error_is_transient(self) -> None:
        assert _is_transient_error(ConnectionError("连接失败")) is True

    def test_os_error_is_transient(self) -> None:
        assert _is_transient_error(OSError("网络不可达")) is True

    def test_httpx_429_is_transient(self) -> None:
        import httpx

        response = httpx.Response(429, request=httpx.Request("GET", "https://x.com"))
        exc = httpx.HTTPStatusError("限流", request=response.request, response=response)
        assert _is_transient_error(exc) is True

    def test_httpx_500_is_transient(self) -> None:
        import httpx

        response = httpx.Response(500, request=httpx.Request("GET", "https://x.com"))
        exc = httpx.HTTPStatusError(
            "服务端错误", request=response.request, response=response
        )
        assert _is_transient_error(exc) is True

    def test_httpx_503_is_transient(self) -> None:
        import httpx

        response = httpx.Response(503, request=httpx.Request("GET", "https://x.com"))
        exc = httpx.HTTPStatusError(
            "不可用", request=response.request, response=response
        )
        assert _is_transient_error(exc) is True

    def test_httpx_400_is_not_transient(self) -> None:
        import httpx

        response = httpx.Response(400, request=httpx.Request("GET", "https://x.com"))
        exc = httpx.HTTPStatusError(
            "客户端错误", request=response.request, response=response
        )
        assert _is_transient_error(exc) is False

    def test_httpx_404_is_not_transient(self) -> None:
        import httpx

        response = httpx.Response(404, request=httpx.Request("GET", "https://x.com"))
        exc = httpx.HTTPStatusError(
            "未找到", request=response.request, response=response
        )
        assert _is_transient_error(exc) is False

    def test_value_error_is_not_transient(self) -> None:
        assert _is_transient_error(ValueError("无效参数")) is False

    def test_runtime_error_is_not_transient(self) -> None:
        assert _is_transient_error(RuntimeError("运行时错误")) is False

    def test_key_error_is_not_transient(self) -> None:
        assert _is_transient_error(KeyError("missing")) is False


async def test_transient_error_schedules_retry(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """瞬态错误（TimeoutError）应递增 consecutive_errors 并安排重试。"""
    scheduler._execution_timeout = 0.01
    job = _make_interval_job("retry-test")
    job.consecutive_errors = 0
    await job_store.upsert(job)

    await scheduler.start()
    try:

        async def slow_invoke(*args, **kwargs):
            await asyncio.sleep(10)
            return {"messages": [MagicMock(content="不应到达")]}

        mock_create = _mock_create_agent()
        mock_create.return_value[0].graph.ainvoke = slow_invoke

        with patch(_PATCH_CREATE_AGENT, mock_create):
            record = await scheduler._execute_job(job)

        assert record.status == "timeout"
        assert job.consecutive_errors == 1

        # 验证 JobStore 中的 consecutive_errors 已更新
        stored = await job_store.get(job.id)
        assert stored is not None
        assert stored.consecutive_errors == 1

        # 验证重试任务已注册到 APScheduler
        retry_id = f"{job.id}-retry-1"
        aps_job = scheduler._aps.get_job(retry_id)
        assert aps_job is not None
    finally:
        await scheduler.stop()


async def test_retry_uses_correct_backoff_intervals(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """重试间隔应按 BACKOFF_INTERVALS 递增。"""
    job = _make_interval_job("backoff-test")
    await job_store.upsert(job)

    await scheduler.start()
    try:
        mock_create = _mock_create_agent()
        mock_create.return_value[0].graph.ainvoke = AsyncMock(
            side_effect=ConnectionError("连接失败")
        )

        # 第 1 次失败 → 退避 30s
        job.consecutive_errors = 0
        with patch(_PATCH_CREATE_AGENT, mock_create):
            await scheduler._execute_job(job)
        assert job.consecutive_errors == 1
        retry_job_1 = scheduler._aps.get_job(f"{job.id}-retry-1")
        assert retry_job_1 is not None

        # 第 2 次失败 → 退避 60s
        with patch(_PATCH_CREATE_AGENT, mock_create):
            await scheduler._execute_job(job)
        assert job.consecutive_errors == 2
        retry_job_2 = scheduler._aps.get_job(f"{job.id}-retry-2")
        assert retry_job_2 is not None

        # 第 3 次失败 → 退避 300s
        with patch(_PATCH_CREATE_AGENT, mock_create):
            await scheduler._execute_job(job)
        assert job.consecutive_errors == 3
        retry_job_3 = scheduler._aps.get_job(f"{job.id}-retry-3")
        assert retry_job_3 is not None
    finally:
        await scheduler.stop()


async def test_retry_exhausted_no_more_retries(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """重试次数耗尽后不再安排重试。"""
    job = _make_interval_job("exhausted-test")
    job.consecutive_errors = MAX_CRON_RETRIES  # 已达上限
    await job_store.upsert(job)

    await scheduler.start()
    try:
        mock_create = _mock_create_agent()
        mock_create.return_value[0].graph.ainvoke = AsyncMock(
            side_effect=ConnectionError("连接失败")
        )

        with patch(_PATCH_CREATE_AGENT, mock_create):
            record = await scheduler._execute_job(job)

        assert record.status == "failed"
        # consecutive_errors 不应再递增（已达上限，不重试）
        assert job.consecutive_errors == MAX_CRON_RETRIES

        # 不应有新的重试任务
        retry_id = f"{job.id}-retry-{MAX_CRON_RETRIES + 1}"
        assert scheduler._aps.get_job(retry_id) is None
    finally:
        await scheduler.stop()


async def test_success_resets_consecutive_errors(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """成功执行后应重置 consecutive_errors 为 0。"""
    job = _make_interval_job("reset-test")
    job.consecutive_errors = 2
    await job_store.upsert(job)

    mock_create = _mock_create_agent("执行成功")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        record = await scheduler._execute_job(job)

    assert record.status == "success"
    assert job.consecutive_errors == 0

    # 验证 JobStore 中也已重置
    stored = await job_store.get(job.id)
    assert stored is not None
    assert stored.consecutive_errors == 0


async def test_permanent_error_no_retry(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """永久错误（如 ValueError）不应触发重试。"""
    job = _make_interval_job("perm-error-test")
    job.consecutive_errors = 0
    await job_store.upsert(job)

    await scheduler.start()
    try:
        mock_create = _mock_create_agent()
        mock_create.return_value[0].graph.ainvoke = AsyncMock(
            side_effect=ValueError("无效参数")
        )

        with patch(_PATCH_CREATE_AGENT, mock_create):
            record = await scheduler._execute_job(job)

        assert record.status == "failed"
        # 永久错误不递增 consecutive_errors，也不安排重试
        assert job.consecutive_errors == 0
        assert scheduler._aps.get_job(f"{job.id}-retry-1") is None
    finally:
        await scheduler.stop()


async def test_success_with_zero_errors_no_upsert(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """consecutive_errors 已为 0 时成功执行不应触发额外的 upsert。"""
    job = _make_interval_job("no-upsert-test")
    job.consecutive_errors = 0

    mock_create = _mock_create_agent("正常输出")

    with (
        patch(_PATCH_CREATE_AGENT, mock_create),
        patch.object(job_store, "upsert", new_callable=AsyncMock) as mock_upsert,
    ):
        await scheduler._execute_job(job)

    # consecutive_errors 为 0 时不需要调用 upsert
    mock_upsert.assert_not_awaited()


# --- trigger() 立即执行测试（7.4）---


async def test_trigger_executes_job_immediately(
    scheduler: Scheduler, job_store: JobStore
) -> None:
    """trigger() 应立即执行指定任务。"""
    job = _make_interval_job("trigger-test")
    await job_store.upsert(job)

    mock_create = _mock_create_agent("立即执行输出")

    with patch(_PATCH_CREATE_AGENT, mock_create):
        await scheduler.trigger(job.id)
        # 等待 task 完成
        await asyncio.sleep(0.1)

    # 验证 Agent 被调用
    mock_create.assert_awaited_once_with(checkpointer=None)

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

        mock_create = _mock_create_agent("不影响调度")

        with patch(_PATCH_CREATE_AGENT, mock_create):
            await scheduler.trigger(job.id)
            await asyncio.sleep(0.1)

        # trigger 后 APScheduler 中的任务状态不变
        aps_job_after = scheduler._aps.get_job(job.id)
        assert aps_job_after is not None
        assert aps_job_after.next_run_time == next_run_before
    finally:
        await scheduler.stop()

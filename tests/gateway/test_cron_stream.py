"""cron 流式 runner：直播事件 + 错误如实上抛（不误记 success）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lumi.gateway.bridge import BridgeEvent, EventKind
from lumi.gateway.cron_stream import build_cron_stream_runner


@pytest.mark.parametrize("stop", [True, False], ids=["user-stop", "timeout"])
async def test_cancelled_run_finishes_observers_after_persisting(tmp_path, stop):
    """复用普通会话的 turn.complete 收尾与历史回读，不让观测会话残留 running。"""
    from lumi.agents.cron.delivery import DeliveryManager
    from lumi.agents.cron.job_store import JobStore
    from lumi.agents.cron.models import Job, Schedule, ScheduleType
    from lumi.agents.cron.run_log import RunLog
    from lumi.agents.cron.scheduler import Scheduler

    started = asyncio.Event()
    persisted = False

    class Bridge(_fake_bridge([])):
        async def stream_response(self, prompt, **kwargs):
            yield BridgeEvent(kind=EventKind.TURN_START)
            yield BridgeEvent(
                kind=EventKind.TOOL_START, name="bash", tool_call_id="tool"
            )
            started.set()
            await asyncio.Event().wait()

        async def finalize_cancelled_stream(self, stream):
            nonlocal persisted
            await super().finalize_cancelled_stream(stream)
            persisted = True

    class Hub(_Hub):
        def publish_thread_event(self, thread_id, frame):
            if frame["params"]["type"] == "turn.complete":
                assert persisted  # 事件到达后前端立即回读，checkpoint 须已落盘
            super().publish_thread_event(thread_id, frame)

    hub = Hub()
    store = JobStore(tmp_path / "jobs.json")
    scheduler = Scheduler(
        store,
        RunLog(tmp_path / "runs"),
        DeliveryManager(),
        build_cron_stream_runner(hub),
        execution_timeout=10 if stop else 0.02,
    )
    job = Job(
        name="cancel",
        schedule=Schedule(type=ScheduleType.INTERVAL, value="5m"),
        prompt="run",
    )
    await store.upsert(job)
    with patch("lumi.gateway.bridge.AgentBridge", Bridge):
        await scheduler.trigger(job.id)
        task = scheduler._running_tasks[job.id]
        await started.wait()
        if stop:
            assert scheduler.cancel_job(job.id)
        record = await task
    assert record.status == ("stopped" if stop else "timeout")
    terminal = [f for f in hub.published if f["params"]["type"] == "turn.complete"]
    assert len(terminal) == 1
    assert terminal[0]["params"]["session_id"] == record.thread_id


class _Hub:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def has_observers(self, thread_id: str) -> bool:
        return True

    def publish_thread_event(self, thread_id: str, frame: dict) -> None:
        self.published.append(frame)


def _fake_bridge(
    events: list[BridgeEvent], final_text: str = "done", pending: tuple = ()
):
    class _Graph:
        async def aget_state(self, config):
            messages = [SimpleNamespace(content=final_text)]
            return SimpleNamespace(next=pending, values={"messages": messages})

    class _FakeBridge:
        graph = _Graph()

        async def initialize(self, **kw) -> None: ...
        def switch_thread(self, thread_id: str) -> None: ...

        async def stream_response(self, prompt, **kw):
            for e in events:
                yield e

        async def finalize_cancelled_stream(self, stream) -> None:
            await stream.aclose()

        async def close(self) -> None: ...

    return _FakeBridge


async def test_runner_raises_on_error_event():
    """stream_response 把异常吞成 ERROR 事件；runner 须补抛，使 scheduler 记 failed。"""
    hub = _Hub()
    events = [
        BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="working"),
        BridgeEvent(kind=EventKind.ERROR, error="boom"),
    ]
    with patch("lumi.gateway.bridge.AgentBridge", _fake_bridge(events)):
        runner = build_cron_stream_runner(hub)
        with pytest.raises(RuntimeError, match="boom"):
            await runner("do it", "cron-x", "")
    # 错误如实展示；结束事件触发持久历史回读，清理中断的流式与工具状态。
    assert [f["params"]["type"] for f in hub.published] == [
        "message.delta",
        "error",
        "turn.complete",
    ]


async def test_runner_skips_publish_without_observers():
    """零观测者时不构帧/不发布（省 token 级 bridge_event_to_wire），但仍跑完。"""

    class _NoObsHub:
        def __init__(self) -> None:
            self.published: list[dict] = []

        def has_observers(self, thread_id: str) -> bool:
            return False

        def publish_thread_event(self, thread_id: str, frame: dict) -> None:
            self.published.append(frame)

    hub = _NoObsHub()
    events = [BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi")]
    with patch("lumi.gateway.bridge.AgentBridge", _fake_bridge(events)):
        runner = build_cron_stream_runner(hub)
        output = await runner("do it", "cron-x", "")
    assert output == "done"
    assert hub.published == []


async def test_runner_returns_output_on_success():
    """无 ERROR 事件时返回终态 output。"""
    hub = _Hub()
    events = [
        BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi"),
        BridgeEvent(kind=EventKind.TURN_COMPLETE),
    ]
    with patch(
        "lumi.gateway.bridge.AgentBridge", _fake_bridge(events, final_text="最终答复")
    ):
        runner = build_cron_stream_runner(hub)
        output = await runner("do it", "cron-x", "")
    assert output == "最终答复"
    assert [f["params"]["type"] for f in hub.published] == [
        "message.delta",
        "turn.complete",
    ]


async def test_unreported_stream_error_finishes_observers():
    class Bridge(_fake_bridge([])):
        async def stream_response(self, prompt, **kwargs):
            yield BridgeEvent(kind=EventKind.TURN_START)
            raise RuntimeError("stream failed")

    hub = _Hub()
    with patch("lumi.gateway.bridge.AgentBridge", Bridge):
        with pytest.raises(RuntimeError, match="stream failed"):
            await build_cron_stream_runner(hub)("do it", "cron-x", "")
    assert [f["params"]["type"] for f in hub.published] == [
        "turn.start",
        "turn.complete",
    ]


async def test_runner_initializes_bridge_in_job_project():
    """任务绑定的项目即会话项目：权限边界 / MCP / 项目说明都按它加载。"""
    captured: dict = {}
    fake = _fake_bridge([BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi")])

    class _Bridge(fake):
        async def initialize(self, **kw) -> None:
            captured.update(kw)

    with patch("lumi.gateway.bridge.AgentBridge", _Bridge):
        await build_cron_stream_runner(_Hub())("do it", "cron-x", "/proj")
    assert captured["project_dir"] == "/proj"


async def test_runner_uses_global_mode_without_approval_channel():
    """cron 不接审批通道，模式交统一路由读取全局设置。"""
    captured: dict = {}
    fake = _fake_bridge([BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi")])

    class _Bridge(fake):
        async def initialize(self, **kw) -> None:
            captured["init"] = kw

        async def stream_response(self, prompt, **kw):
            captured["stream"] = kw
            async for e in fake.stream_response(self, prompt, **kw):
                yield e

    with patch("lumi.gateway.bridge.AgentBridge", _Bridge):
        await build_cron_stream_runner(_Hub())("do it", "cron-x", "/proj")
    assert captured["init"]["interactive"] is False
    assert "tool_mode" not in captured["stream"]


async def test_drained_run_is_not_reported_as_success():
    """停机 drain 让图停在边界（next 非空）：按取消处理，不把半截输出记成 success。"""
    events = [BridgeEvent(kind=EventKind.MESSAGE_COMPLETE)]
    bridge = _fake_bridge(events, final_text="file_a", pending=("CallModel",))
    with patch("lumi.gateway.bridge.AgentBridge", bridge):
        runner = build_cron_stream_runner(_Hub())
        with pytest.raises(asyncio.CancelledError):
            await runner("do it", "cron-x", "")

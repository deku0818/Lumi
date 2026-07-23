"""cron 流式 runner：直播事件 + 错误如实上抛（不误记 success）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lumi.gateway.bridge import BridgeEvent, EventKind
from lumi.gateway.cron_stream import build_cron_stream_runner


class _Hub:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def has_observers(self, thread_id: str) -> bool:
        return True

    def publish_thread_event(self, thread_id: str, frame: dict) -> None:
        self.published.append(frame)


def _fake_bridge(events: list[BridgeEvent], final_text: str = "done"):
    class _FakeBridge:
        async def initialize(self, **kw) -> None: ...
        def switch_thread(self, thread_id: str) -> None: ...

        async def stream_response(self, prompt, **kw):
            for e in events:
                yield e

        async def snapshot_messages(self):
            return [SimpleNamespace(content=final_text)]

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
            await runner("do it", "cron-x")
    # 错误事件仍直播给观测者（前端能看到），只是额外补抛让状态如实
    assert len(hub.published) == 2


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
        output = await runner("do it", "cron-x")
    assert output == "done"
    assert hub.published == []


async def test_runner_returns_output_on_success():
    """无 ERROR 事件时返回终态 output。"""
    hub = _Hub()
    events = [BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="hi")]
    with patch(
        "lumi.gateway.bridge.AgentBridge", _fake_bridge(events, final_text="最终答复")
    ):
        runner = build_cron_stream_runner(hub)
        output = await runner("do it", "cron-x")
    assert output == "最终答复"
    assert len(hub.published) == 1

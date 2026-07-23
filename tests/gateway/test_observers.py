"""ThreadObserverHub：cron 执行直播的按 thread pub/sub。"""

from __future__ import annotations

import asyncio

from lumi.gateway.observers import ThreadObserverHub


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)


async def test_publish_reaches_observer_in_order():
    hub = ThreadObserverHub()
    ch = _FakeChannel()
    hub.add("cron-x", ch)
    hub.publish("cron-x", {"n": 1})
    hub.publish("cron-x", {"n": 2})
    await asyncio.sleep(0.02)
    assert ch.sent == [{"n": 1}, {"n": 2}]


async def test_no_observer_is_noop():
    hub = ThreadObserverHub()
    hub.publish("cron-x", {"n": 1})  # 不报错、不建任何东西
    assert not hub.has_observers("cron-x")


async def test_multi_observer_each_receives():
    hub = ThreadObserverHub()
    a, b = _FakeChannel(), _FakeChannel()
    hub.add("cron-x", a)
    hub.add("cron-x", b)
    hub.publish("cron-x", {"n": 1})
    await asyncio.sleep(0.02)
    assert a.sent == [{"n": 1}]
    assert b.sent == [{"n": 1}]


async def test_remove_stops_delivery():
    hub = ThreadObserverHub()
    ch = _FakeChannel()
    hub.add("cron-x", ch)
    hub.remove("cron-x", ch)
    hub.publish("cron-x", {"n": 1})
    await asyncio.sleep(0.02)
    assert ch.sent == []
    assert not hub.has_observers("cron-x")


async def test_remove_channel_clears_all_threads():
    hub = ThreadObserverHub()
    ch = _FakeChannel()
    hub.add("cron-a", ch)
    hub.add("cron-b", ch)
    hub.remove_channel(ch)
    assert not hub.has_observers("cron-a")
    assert not hub.has_observers("cron-b")


async def test_backpressure_never_blocks_publisher():
    """慢观测者（send 永不返回）时 publish 仍非阻塞：满即丢，发布方绝不背压。"""
    hub = ThreadObserverHub()

    class _StuckChannel:
        async def send(self, frame: dict) -> None:
            await asyncio.Event().wait()  # 永不返回，模拟卡死的连接

    hub.add("cron-x", _StuckChannel())
    # 远超队列上限（256）的帧：全部 put_nowait / drop-oldest，无一阻塞。
    for i in range(2000):
        hub.publish("cron-x", {"n": i})
    # 走到这里没卡死即证明发布不背压

"""双阈值定时节流器：``elapsed >= min_ms`` 或 ``pending >= min_chars`` 任一满足即 fire。

与"被动节流"（只在下一个 delta 到来时才比较时间窗）最关键的区别：本节流器用
``loop.call_later`` **主动注册定时器**——即使上游静默（工具执行 / 网络抖动 / 思考间隙），
定时器到点仍会 fire 一次，把累积尾部刷出。从根上消除"回复卡在半句、工具跑完才一次性
蹦出剩余"。

接口：
- ``note(delta_chars)``：记录新增内容；可能立即 fire 或注册定时器
- ``dispose()``：取消未触发的定时器（终态 / 驱逐用）

``on_fire`` 是同步回调，调用方需保证它只做"快照 + 入队"而不阻塞（真正的 HTTP 往返
交给 UpdateQueue 异步执行）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class Throttle:
    def __init__(
        self,
        *,
        min_ms: int,
        min_chars: int,
        on_fire: Callable[[], None],
    ) -> None:
        self._min_ms = min_ms
        self._min_chars = min_chars
        self._on_fire = on_fire
        self._pending_chars = 0
        self._last_fire_ms = 0
        self._timer: asyncio.TimerHandle | None = None
        self._running = False

    def note(self, delta_chars: int) -> None:
        self._pending_chars += max(0, delta_chars)
        if self._running:
            # 已有一次 fire 在进行；当前 on_fire 会把最新内容一起带走
            return
        if self._pending_chars >= self._min_chars:
            self._cancel_timer()
            self._do_fire()
            return
        if self._timer is not None:
            return
        now = _now_ms()
        wait_ms = max(0, self._min_ms - (now - self._last_fire_ms))
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(wait_ms / 1000.0, self._do_fire)

    def dispose(self) -> None:
        self._cancel_timer()

    def _do_fire(self) -> None:
        # 在同步 on_fire 之前先清状态：若回调里重入 note（少见但合法），状态需一致，
        # 且不希望 fire 期间 self._timer 仍看起来"在排队"。
        self._timer = None
        self._pending_chars = 0
        self._last_fire_ms = _now_ms()
        self._running = True
        try:
            self._on_fire()
        finally:
            self._running = False

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()  # TimerHandle.cancel 幂等且不抛
            self._timer = None


def _now_ms() -> int:
    # monotonic 不受 NTP 步进 / DST 跳变影响；区间节流必须用它而非 time.time()。
    return int(time.monotonic() * 1000)

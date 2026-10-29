"""Dream 预览复用服务器调度规则，系统时区的目标日期偏移随响应下发。"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from lumi.gateway import channel_rpc


@pytest.fixture
def server_zone(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("系统不支持进程级 TZ 切换")
    with monkeypatch.context() as patch:

        def set_zone(zone: str) -> None:
            patch.setenv("TZ", zone)
            time.tzset()

        yield set_zone
    time.tzset()


@pytest.mark.parametrize(
    ("zone", "now", "hhmm", "expected", "remaining"),
    [
        ("UTC", "2026-10-29T01:00:00", "03:00", "2026-10-29T03:00:00+00:00", 7200),
        ("UTC", "2026-10-29T03:00:00", "03:00", "2026-10-30T03:00:00+00:00", 86400),
        (
            "Asia/Shanghai",
            "2026-10-29T20:00:00",
            "03:00",
            "2026-10-30T03:00:00+08:00",
            25200,
        ),
        # 今晚仍是夏令时，但明天 03:00 已是冬令时，不能拿当前偏移推算。
        (
            "America/New_York",
            "2026-10-31T23:00:00",
            "03:00",
            "2026-11-01T03:00:00-05:00",
            18000,
        ),
        (
            "America/New_York",
            "2027-03-13T23:00:00",
            "02:30",
            "2027-03-14T03:00:00-04:00",
            10800,
        ),
        # Lord Howe 只跳过半小时，不能写死按一个小时修正。
        (
            "Australia/Lord_Howe",
            "2026-10-03T23:00:00",
            "02:15",
            "2026-10-04T02:30:00+11:00",
            10800,
        ),
    ],
)
async def test_preview_uses_server_clock_and_target_offset(
    server_zone, monkeypatch, zone, now, hhmm, expected, remaining
):
    server_zone(zone)

    class Clock(datetime):
        @classmethod
        def now(cls):
            return cls.fromisoformat(now)

    monkeypatch.setattr(channel_rpc, "datetime", Clock)
    result = await channel_rpc.HANDLERS["get_daily_dream_schedule"]({"time": hhmm})
    assert result["next_run_at"] == expected
    assert result["timezone"] == datetime.fromisoformat(expected).astimezone().tzname()
    assert result["refresh_after_seconds"] == remaining


@pytest.mark.parametrize(
    ("start", "hhmm", "expected", "remaining"),
    [
        (datetime(2027, 3, 13, 23), "02:30", "2027-03-14T03:00:00-04:00", 10800),
        # 回拨后第二次 01:10：不能在重建目标时间时丢掉 fold，预览成已过去的第一次 01:30。
        (
            datetime(2026, 11, 1, 1, 10, fold=1),
            "01:30",
            "2026-11-01T01:30:00-05:00",
            1200,
        ),
    ],
)
async def test_dst_preview_matches_real_scheduler_trigger(
    server_zone, monkeypatch, start, hhmm, expected, remaining
):
    from lumi.gateway.channels.feishu import daily_dream

    server_zone("America/New_York")
    clock = start.timestamp()
    triggered = []

    class Clock(datetime):
        @classmethod
        def now(cls):
            return cls.fromtimestamp(clock)

    async def advance(seconds):
        nonlocal clock
        clock += seconds

    async def cycle(*args):
        triggered.append(Clock.now().astimezone().isoformat())
        raise asyncio.CancelledError

    monkeypatch.setattr(channel_rpc, "datetime", Clock)
    monkeypatch.setattr(daily_dream, "datetime", Clock)
    monkeypatch.setattr(
        daily_dream,
        "asyncio",
        SimpleNamespace(sleep=advance, CancelledError=asyncio.CancelledError),
    )
    monkeypatch.setattr(daily_dream, "_run_cycle", cycle)
    preview = await channel_rpc.HANDLERS["get_daily_dream_schedule"]({"time": hhmm})
    config = SimpleNamespace(daily_dream_enabled=True, daily_dream_time=hhmm)
    with pytest.raises(asyncio.CancelledError):
        await daily_dream.daily_dream_loop(None, config, "test")
    assert triggered == [preview["next_run_at"]] == [expected]
    assert preview["refresh_after_seconds"] == remaining


@pytest.mark.parametrize("hhmm", ["", "24:00", "03:60", "abc"])
async def test_invalid_time_cannot_produce_a_preview(hhmm):
    with pytest.raises(ValueError):
        await channel_rpc.HANDLERS["get_daily_dream_schedule"]({"time": hhmm})

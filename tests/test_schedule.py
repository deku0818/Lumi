"""Schedule 调度规则解析与 trigger 转换测试。"""

from datetime import datetime

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from lumi.agents.cron.models import (
    Schedule,
    ScheduleType,
    parse_interval_to_seconds,
)

# === parse_interval_to_seconds ===


class TestParseIntervalToSeconds:
    """间隔简写解析测试。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1s", 1),
            ("30s", 30),
            ("5m", 300),
            ("2h", 7200),
            ("1d", 86400),
            ("100s", 100),
        ],
    )
    def test_valid_intervals(self, value: str, expected: int) -> None:
        assert parse_interval_to_seconds(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["0s", "abc", "5x", "m5", "", "5", "5mm", "-1m"],
    )
    def test_invalid_intervals(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_interval_to_seconds(value)


# === Schedule.parse ===


class TestScheduleParse:
    """Schedule.parse 自动识别测试。"""

    def test_parse_interval(self) -> None:
        s = Schedule.parse("30m")
        assert s.type == ScheduleType.INTERVAL
        assert s.value == "30m"

    def test_parse_iso8601(self) -> None:
        s = Schedule.parse("2025-01-15T09:00:00")
        assert s.type == ScheduleType.AT
        assert s.value == "2025-01-15T09:00:00"

    def test_parse_iso8601_date_only(self) -> None:
        s = Schedule.parse("2025-01-15")
        assert s.type == ScheduleType.AT

    def test_parse_cron(self) -> None:
        s = Schedule.parse("*/5 * * * *")
        assert s.type == ScheduleType.CRON
        assert s.value == "*/5 * * * *"

    def test_parse_cron_daily_9am(self) -> None:
        s = Schedule.parse("0 9 * * *")
        assert s.type == ScheduleType.CRON

    def test_parse_strips_whitespace(self) -> None:
        s = Schedule.parse("  5m  ")
        assert s.type == ScheduleType.INTERVAL
        assert s.value == "5m"

    @pytest.mark.parametrize(
        "raw",
        ["", "  ", "abc", "every 5 minutes", "* * *", "0m"],
    )
    def test_parse_invalid_raises(self, raw: str) -> None:
        with pytest.raises(ValueError):
            Schedule.parse(raw)


# === Schedule.to_trigger ===


class TestScheduleToTrigger:
    """Schedule.to_trigger 转换测试。"""

    def test_at_trigger(self) -> None:
        s = Schedule(type=ScheduleType.AT, value="2025-06-15T10:30:00")
        trigger = s.to_trigger()
        assert isinstance(trigger, DateTrigger)
        assert (
            trigger.run_date
            == datetime.fromisoformat("2025-06-15T10:30:00").astimezone()
        )

    def test_interval_trigger(self) -> None:
        s = Schedule(type=ScheduleType.INTERVAL, value="2h")
        trigger = s.to_trigger()
        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval.total_seconds() == 7200

    def test_cron_trigger(self) -> None:
        s = Schedule(type=ScheduleType.CRON, value="0 9 * * 1-5")
        trigger = s.to_trigger()
        assert isinstance(trigger, CronTrigger)


# === frozen 不可变性 ===


class TestScheduleImmutability:
    """Schedule 不可变性测试。"""

    def test_frozen(self) -> None:
        s = Schedule(type=ScheduleType.INTERVAL, value="5m")
        with pytest.raises(AttributeError):
            s.type = ScheduleType.CRON  # type: ignore[misc]

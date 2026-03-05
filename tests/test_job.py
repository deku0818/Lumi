"""Job 数据模型序列化/反序列化测试。"""

from datetime import datetime

from lumi.agents.cron.models import Job, Schedule, ScheduleType


class TestJobDefaults:
    """Job 默认值测试。"""

    def test_id_auto_generated(self) -> None:
        job = Job(
            name="测试", schedule=Schedule(ScheduleType.INTERVAL, "5m"), prompt="hi"
        )
        assert len(job.id) == 12

    def test_unique_ids(self) -> None:
        jobs = [
            Job(name="a", schedule=Schedule(ScheduleType.INTERVAL, "5m"), prompt="hi")
            for _ in range(10)
        ]
        ids = {j.id for j in jobs}
        assert len(ids) == 10

    def test_enabled_default_true(self) -> None:
        job = Job(name="t", schedule=Schedule(ScheduleType.INTERVAL, "1m"), prompt="p")
        assert job.enabled is True

    def test_consecutive_errors_default_zero(self) -> None:
        job = Job(name="t", schedule=Schedule(ScheduleType.INTERVAL, "1m"), prompt="p")
        assert job.consecutive_errors == 0

    def test_created_at_auto(self) -> None:
        before = datetime.now()
        job = Job(name="t", schedule=Schedule(ScheduleType.INTERVAL, "1m"), prompt="p")
        after = datetime.now()
        assert before <= job.created_at <= after


class TestJobSerialization:
    """Job to_dict / from_dict round-trip 测试。"""

    def _make_job(self) -> Job:
        return Job(
            id="abc123def456",
            name="每日摘要",
            schedule=Schedule(type=ScheduleType.CRON, value="0 9 * * *"),
            prompt="请汇总今天的待办事项",
            enabled=True,
            created_at=datetime(2025, 1, 15, 8, 0, 0),
            consecutive_errors=2,
        )

    def test_to_dict_structure(self) -> None:
        d = self._make_job().to_dict()
        assert d["id"] == "abc123def456"
        assert d["name"] == "每日摘要"
        assert d["schedule"] == {"type": "cron", "value": "0 9 * * *"}
        assert d["prompt"] == "请汇总今天的待办事项"
        assert d["enabled"] is True
        assert d["created_at"] == "2025-01-15T08:00:00"
        assert d["consecutive_errors"] == 2

    def test_from_dict(self) -> None:
        data = {
            "id": "abc123def456",
            "name": "每日摘要",
            "schedule": {"type": "cron", "value": "0 9 * * *"},
            "prompt": "请汇总今天的待办事项",
            "enabled": True,
            "created_at": "2025-01-15T08:00:00",
            "consecutive_errors": 2,
        }
        job = Job.from_dict(data)
        assert job.id == "abc123def456"
        assert job.name == "每日摘要"
        assert job.schedule == Schedule(type=ScheduleType.CRON, value="0 9 * * *")
        assert job.prompt == "请汇总今天的待办事项"
        assert job.enabled is True
        assert job.created_at == datetime(2025, 1, 15, 8, 0, 0)
        assert job.consecutive_errors == 2

    def test_round_trip(self) -> None:
        original = self._make_job()
        restored = Job.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.schedule == original.schedule
        assert restored.prompt == original.prompt
        assert restored.enabled == original.enabled
        assert restored.created_at == original.created_at
        assert restored.consecutive_errors == original.consecutive_errors

    def test_from_dict_defaults_optional_fields(self) -> None:
        """enabled 和 consecutive_errors 缺失时使用默认值。"""
        data = {
            "id": "x",
            "name": "n",
            "schedule": {"type": "interval", "value": "5m"},
            "prompt": "p",
            "created_at": "2025-01-01T00:00:00",
        }
        job = Job.from_dict(data)
        assert job.enabled is True
        assert job.consecutive_errors == 0

    def test_round_trip_all_schedule_types(self) -> None:
        """三种调度类型都能正确 round-trip。"""
        for stype, sval in [
            (ScheduleType.AT, "2025-06-15T10:30:00"),
            (ScheduleType.INTERVAL, "2h"),
            (ScheduleType.CRON, "*/5 * * * *"),
        ]:
            job = Job(
                name="test",
                schedule=Schedule(type=stype, value=sval),
                prompt="p",
            )
            restored = Job.from_dict(job.to_dict())
            assert restored.schedule.type == stype
            assert restored.schedule.value == sval

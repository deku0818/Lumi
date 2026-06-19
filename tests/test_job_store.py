"""JobStore 持久化测试。"""

import json
from datetime import datetime
from pathlib import Path

from lumi.agents.cron.job_store import _FORMAT_VERSION, JobStore
from lumi.agents.cron.models import Job, Schedule, ScheduleType


def _make_job(
    job_id: str = "test123456ab",
    name: str = "测试任务",
    schedule_type: ScheduleType = ScheduleType.CRON,
    schedule_value: str = "0 9 * * *",
    prompt: str = "请执行测试",
) -> Job:
    return Job(
        id=job_id,
        name=name,
        schedule=Schedule(type=schedule_type, value=schedule_value),
        prompt=prompt,
        created_at=datetime(2025, 1, 15, 8, 0, 0),
    )


class TestJobStoreLoad:
    """load() 方法测试。"""

    async def test_load_file_not_exists(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "nonexistent" / "jobs.json")
        result = await store.load()
        assert result == []

    async def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        path.write_text("", encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert result == []

    async def test_load_whitespace_only_file(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        path.write_text("   \n  ", encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert result == []

    async def test_load_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        data = {
            "version": 1,
            "jobs": [_make_job().to_dict()],
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert len(result) == 1
        assert result[0].id == "test123456ab"
        assert result[0].name == "测试任务"

    async def test_load_corrupt_file_backs_up(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        path.write_text("{invalid json!!!", encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert result == []
        # 原文件应被备份
        bak_path = path.with_suffix(".bak")
        assert bak_path.exists()
        assert bak_path.read_text(encoding="utf-8") == "{invalid json!!!"
        # 原文件应不存在（被 rename 了）
        assert not path.exists()

    async def test_load_corrupt_json_structure(self, tmp_path: Path) -> None:
        """JSON 合法但结构不符（如 jobs 中缺少必要字段）。"""
        path = tmp_path / "jobs.json"
        data = {"version": 1, "jobs": [{"bad": "data"}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert result == []
        assert path.with_suffix(".bak").exists()

    async def test_load_empty_jobs_array(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        data = {"version": 1, "jobs": []}
        path.write_text(json.dumps(data), encoding="utf-8")
        store = JobStore(path)
        result = await store.load()
        assert result == []


class TestJobStoreSave:
    """save() 方法测试。"""

    async def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "jobs.json"
        store = JobStore(path)
        await store.save([_make_job()])
        assert path.exists()

    async def test_save_format_has_version(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        store = JobStore(path)
        await store.save([_make_job()])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == _FORMAT_VERSION

    async def test_save_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        store = JobStore(path)
        await store.save([])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == _FORMAT_VERSION
        assert data["jobs"] == []

    async def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "jobs.json"
        store = JobStore(path)
        await store.save([_make_job(job_id="aaa")])
        await store.save([_make_job(job_id="bbb")])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["id"] == "bbb"


class TestJobStoreUpsert:
    """upsert() 方法测试。"""

    async def test_upsert_creates_new(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        job = _make_job()
        await store.upsert(job)
        result = await store.get_all()
        assert len(result) == 1
        assert result[0].id == job.id

    async def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        job = _make_job()
        await store.upsert(job)
        updated = _make_job(name="更新后的名称")
        await store.upsert(updated)
        result = await store.get_all()
        assert len(result) == 1
        assert result[0].name == "更新后的名称"

    async def test_upsert_multiple_jobs(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        await store.upsert(_make_job(job_id="aaa"))
        await store.upsert(_make_job(job_id="bbb"))
        result = await store.get_all()
        assert len(result) == 2


class TestJobStoreDelete:
    """delete() 方法测试。"""

    async def test_delete_existing(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        await store.upsert(_make_job(job_id="aaa"))
        assert await store.delete("aaa") is True
        assert await store.get_all() == []

    async def test_delete_nonexistent(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        assert await store.delete("nonexistent") is False

    async def test_delete_preserves_others(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        await store.upsert(_make_job(job_id="aaa"))
        await store.upsert(_make_job(job_id="bbb"))
        await store.delete("aaa")
        result = await store.get_all()
        assert len(result) == 1
        assert result[0].id == "bbb"


class TestJobStoreGet:
    """get() 和 get_all() 方法测试。"""

    async def test_get_existing(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        job = _make_job()
        await store.upsert(job)
        result = await store.get(job.id)
        assert result is not None
        assert result.id == job.id
        assert result.name == job.name

    async def test_get_nonexistent(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        assert await store.get("nonexistent") is None

    async def test_get_all_empty(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        assert await store.get_all() == []

    async def test_get_all_returns_all(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs.json")
        await store.upsert(_make_job(job_id="aaa"))
        await store.upsert(_make_job(job_id="bbb"))
        await store.upsert(_make_job(job_id="ccc"))
        result = await store.get_all()
        assert len(result) == 3
        ids = {j.id for j in result}
        assert ids == {"aaa", "bbb", "ccc"}

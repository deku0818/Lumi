"""RunLog 执行日志测试。"""

import json
from datetime import datetime
from pathlib import Path

from lumi.agents.cron.run_log import RunLog, RunRecord
from lumi.utils.constants import MAX_RUN_LOG_FILE_SIZE


def _make_record(
    job_id: str = "test123456ab",
    job_name: str = "测试任务",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    status: str = "success",
    duration_ms: int = 5000,
    output_summary: str = "执行完成",
    error: str = "",
    thread_id: str = "",
) -> RunRecord:
    return RunRecord(
        job_id=job_id,
        job_name=job_name,
        started_at=started_at or datetime(2025, 1, 15, 9, 0, 0),
        finished_at=finished_at or datetime(2025, 1, 15, 9, 0, 5),
        status=status,
        duration_ms=duration_ms,
        output_summary=output_summary,
        error=error,
        thread_id=thread_id,
    )


class TestRunRecordSerialization:
    """RunRecord 序列化/反序列化测试。"""

    def test_to_dict(self) -> None:
        record = _make_record()
        d = record.to_dict()
        assert d["job_id"] == "test123456ab"
        assert d["status"] == "success"
        assert d["started_at"] == "2025-01-15T09:00:00"
        assert d["error"] == ""

    def test_from_dict_roundtrip(self) -> None:
        record = _make_record(error="出错了")
        d = record.to_dict()
        restored = RunRecord.from_dict(d)
        assert restored == record

    def test_from_dict_missing_error_defaults_empty(self) -> None:
        d = _make_record().to_dict()
        del d["error"]
        restored = RunRecord.from_dict(d)
        assert restored.error == ""


class TestRunLogAppendAndGetRecent:
    """append() 和 get_recent() 方法测试。"""

    async def test_append_creates_file(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        record = _make_record()
        await log.append(record)
        path = tmp_path / f"{record.job_id}.jsonl"
        assert path.exists()

    async def test_append_then_get_recent(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        record = _make_record()
        await log.append(record)
        results = await log.get_recent("test123456ab")
        assert len(results) == 1
        assert results[0] == record

    async def test_get_recent_empty(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        results = await log.get_recent("nonexistent")
        assert results == []

    async def test_get_recent_returns_reverse_chronological(
        self, tmp_path: Path
    ) -> None:
        log = RunLog(tmp_path)
        r1 = _make_record(started_at=datetime(2025, 1, 15, 8, 0, 0))
        r2 = _make_record(started_at=datetime(2025, 1, 15, 9, 0, 0))
        r3 = _make_record(started_at=datetime(2025, 1, 15, 10, 0, 0))
        await log.append(r1)
        await log.append(r2)
        await log.append(r3)
        results = await log.get_recent("test123456ab")
        assert results[0].started_at == datetime(2025, 1, 15, 10, 0, 0)
        assert results[1].started_at == datetime(2025, 1, 15, 9, 0, 0)
        assert results[2].started_at == datetime(2025, 1, 15, 8, 0, 0)

    async def test_get_recent_respects_limit(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        for i in range(10):
            r = _make_record(started_at=datetime(2025, 1, 15, i, 0, 0))
            await log.append(r)
        results = await log.get_recent("test123456ab", limit=3)
        assert len(results) == 3
        # 最近的 3 条
        assert results[0].started_at == datetime(2025, 1, 15, 9, 0, 0)

    async def test_get_recent_isolates_by_job_id(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        await log.append(_make_record(job_id="aaa"))
        await log.append(_make_record(job_id="bbb"))
        results_a = await log.get_recent("aaa")
        results_b = await log.get_recent("bbb")
        assert len(results_a) == 1
        assert results_a[0].job_id == "aaa"
        assert len(results_b) == 1
        assert results_b[0].job_id == "bbb"

    async def test_append_multiple_records_jsonl_format(self, tmp_path: Path) -> None:
        """验证文件是 JSONL 格式（每行一条 JSON）。"""
        log = RunLog(tmp_path)
        await log.append(_make_record(output_summary="第一条"))
        await log.append(_make_record(output_summary="第二条"))
        path = tmp_path / "test123456ab.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["output_summary"] == "第一条"
        assert json.loads(lines[1])["output_summary"] == "第二条"


class TestRunLogTrim:
    """超过 2MB 自动裁剪测试。"""

    async def test_trim_when_exceeds_max_size(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        job_id = "trimtest"
        path = tmp_path / f"{job_id}.jsonl"

        # 写入大量记录直到超过 2MB
        record = _make_record(job_id=job_id, output_summary="x" * 400)
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        line_size = len(line.encode("utf-8"))
        # 需要的行数：略超过 2MB
        num_lines = (MAX_RUN_LOG_FILE_SIZE // line_size) + 10

        # 直接写入大文件（避免逐条 append 太慢）
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(num_lines):
                f.write(line)

        assert path.stat().st_size > MAX_RUN_LOG_FILE_SIZE

        # 再 append 一条触发裁剪
        new_record = _make_record(job_id=job_id, output_summary="新记录")
        await log.append(new_record)

        # 裁剪后文件应小于原始大小
        new_size = path.stat().st_size
        assert new_size < MAX_RUN_LOG_FILE_SIZE * 1.1  # 允许少量误差

    async def test_trim_preserves_recent_records(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        job_id = "trimkeep"
        path = tmp_path / f"{job_id}.jsonl"

        # 写入大量记录
        record = _make_record(job_id=job_id, output_summary="x" * 400)
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        line_size = len(line.encode("utf-8"))
        num_lines = (MAX_RUN_LOG_FILE_SIZE // line_size) + 10

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(num_lines):
                f.write(line)

        # append 触发裁剪
        new_record = _make_record(
            job_id=job_id,
            output_summary="最新记录",
            started_at=datetime(2099, 12, 31, 23, 59, 59),
        )
        await log.append(new_record)

        # 最新记录应该还在
        results = await log.get_recent(job_id, limit=1)
        assert len(results) == 1
        assert results[0].output_summary == "最新记录"


class TestRunLogEdgeCases:
    """边界情况测试。"""

    async def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        """JSONL 中有损坏行时应跳过，不影响其他记录。"""
        log = RunLog(tmp_path)
        path = tmp_path / "corrupt.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        valid_record = _make_record(job_id="corrupt")
        valid_line = json.dumps(valid_record.to_dict(), ensure_ascii=False)
        with open(path, "w", encoding="utf-8") as f:
            f.write(valid_line + "\n")
            f.write("{bad json!!!\n")
            f.write(valid_line + "\n")

        results = await log.get_recent("corrupt")
        assert len(results) == 2

    async def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        log = RunLog(tmp_path)
        results = await log.get_recent("empty")
        assert results == []

    async def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        path = tmp_path / "blanks.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        valid_record = _make_record(job_id="blanks")
        valid_line = json.dumps(valid_record.to_dict(), ensure_ascii=False)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n\n")
            f.write(valid_line + "\n")
            f.write("\n")

        results = await log.get_recent("blanks")
        assert len(results) == 1


class TestPruneThreadIds:
    """会话保留策略测试。"""

    async def test_prune_beyond_keep(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        for i in range(5):
            await log.append(
                _make_record(
                    job_id="prune",
                    started_at=datetime(2025, 1, 15, 9, i, 0),
                    thread_id=f"cron-{i}",
                )
            )

        pruned = await log.prune_thread_ids("prune", keep=3)

        # 最旧的两条（i=0,1）被清理
        assert pruned == ["cron-1", "cron-0"]
        records = await log.get_recent("prune", limit=10)
        assert len(records) == 5  # 记录本身保留
        assert [r.thread_id for r in records] == ["cron-4", "cron-3", "cron-2", "", ""]

    async def test_prune_idempotent(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        for i in range(4):
            await log.append(
                _make_record(
                    job_id="idem",
                    started_at=datetime(2025, 1, 15, 9, i, 0),
                    thread_id=f"cron-{i}",
                )
            )
        first = await log.prune_thread_ids("idem", keep=2)
        second = await log.prune_thread_ids("idem", keep=2)
        assert len(first) == 2
        assert second == []

    async def test_prune_nothing_to_do(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        await log.append(_make_record(job_id="few", thread_id="cron-x"))
        assert await log.prune_thread_ids("few", keep=50) == []


class TestDeleteLog:
    """日志删除测试。"""

    async def test_delete_log_removes_file(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        await log.append(_make_record(job_id="gone"))
        assert (tmp_path / "gone.jsonl").exists()
        await log.delete_log("gone")
        assert not (tmp_path / "gone.jsonl").exists()

    async def test_delete_log_missing_file_noop(self, tmp_path: Path) -> None:
        log = RunLog(tmp_path)
        await log.delete_log("never-existed")  # 不抛异常

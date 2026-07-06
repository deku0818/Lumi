"""Dream 状态（sqlite dream_state.db）+ 进程内并发/节流 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumi.agents.memory import dream_lock
from lumi.utils.config import global_models


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """每测试用独立 tmp dream_state.db：重定向 checkpoint dir + 重置模块级连接/进程态。"""
    monkeypatch.setattr(
        global_models.GlobalConfig, "get_checkpoint_dir", lambda self: tmp_path
    )
    dream_lock._conn = None
    dream_lock._in_flight.clear()
    dream_lock._last_scan.clear()
    dream_lock._project_locks.clear()
    yield
    if dream_lock._conn is not None:
        dream_lock._conn.close()
        dream_lock._conn = None


def test_last_at_records_snapshot_ts(tmp_path):
    proj = tmp_path / "proj"
    assert dream_lock.read_last_at(proj) == 0.0  # 空库
    dream_lock.record_dream(proj, 1234.5)
    assert dream_lock.read_last_at(proj) == 1234.5  # 写入的是快照时刻，原值取回


def test_thread_dreamed_at_roundtrip(tmp_path):
    proj = tmp_path / "proj"
    assert dream_lock.read_thread_dreamed_at(proj, "feishu-a") == 0.0  # 空库
    dream_lock.record_thread_dream(proj, "feishu-a", 100.0)
    dream_lock.record_thread_dream(proj, "feishu-b", 200.0)
    assert dream_lock.read_thread_dreamed_at(proj, "feishu-a") == 100.0
    assert dream_lock.read_thread_dreamed_at(proj, "feishu-b") == 200.0
    # 同 thread 重写覆盖
    dream_lock.record_thread_dream(proj, "feishu-a", 300.0)
    assert dream_lock.read_thread_dreamed_at(proj, "feishu-a") == 300.0


def test_per_project_isolation(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    dream_lock.record_dream(a, 1.0)
    dream_lock.record_thread_dream(a, "t", 2.0)
    assert dream_lock.read_last_at(b) == 0.0  # 别的 project 互不影响
    assert dream_lock.read_thread_dreamed_at(b, "t") == 0.0


def test_in_flight():
    proj = Path("/proj-x")
    assert not dream_lock.is_in_flight(proj)
    dream_lock.mark_in_flight(proj)
    assert dream_lock.is_in_flight(proj)
    dream_lock.clear_in_flight(proj)
    assert not dream_lock.is_in_flight(proj)


async def test_in_flight_reflects_project_lock():
    """project_lock 被持有时 is_in_flight 也为真——门控/快返对「正在跑」同样生效。"""
    proj = Path("/proj-y")
    lock = dream_lock.project_lock(proj)
    assert dream_lock.project_lock(proj) is lock  # 同 project 恒同一把锁
    async with lock:
        assert dream_lock.is_in_flight(proj)
    assert not dream_lock.is_in_flight(proj)


def test_throttle_scan(tmp_path):
    proj = tmp_path / "proj"
    assert dream_lock.throttle_scan(proj, 600) is False  # 首次放行
    assert dream_lock.throttle_scan(proj, 600) is True  # 紧接被节流
    assert dream_lock.throttle_scan(proj, 0) is False  # 间隔 0 不节流

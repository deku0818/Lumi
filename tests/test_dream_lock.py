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
    yield
    if dream_lock._conn is not None:
        dream_lock._conn.close()
        dream_lock._conn = None


def test_last_at(tmp_path):
    proj = tmp_path / "proj"
    assert dream_lock.read_last_at(proj) == 0.0  # 空库
    dream_lock.record_dream(proj, {})
    assert dream_lock.read_last_at(proj) > 0


def test_cursors_upsert_preserves_dormant(tmp_path):
    """upsert：更新参与的会话游标，保留没参与的（核心 bug 修复——不再覆盖式误删）。"""
    proj = tmp_path / "proj"
    assert dream_lock.load_cursors(proj) == {}  # 空库
    dream_lock.record_dream(proj, {"t1": 5, "t2": 3})
    assert dream_lock.load_cursors(proj) == {"t1": 5, "t2": 3}
    # 这轮只 t1 参与 → 只更新 t1；t2（dormant 会话）游标必须保留，否则它下次活动时旧消息污染
    dream_lock.record_dream(proj, {"t1": 7})
    assert dream_lock.load_cursors(proj) == {"t1": 7, "t2": 3}


def test_per_project_isolation(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    dream_lock.record_dream(a, {"t": 1})
    assert dream_lock.load_cursors(b) == {}  # 别的 project 互不影响
    assert dream_lock.read_last_at(b) == 0.0


def test_in_flight():
    proj = Path("/proj-x")
    assert not dream_lock.is_in_flight(proj)
    dream_lock.mark_in_flight(proj)
    assert dream_lock.is_in_flight(proj)
    dream_lock.clear_in_flight(proj)
    assert not dream_lock.is_in_flight(proj)


def test_throttle_scan(tmp_path):
    proj = tmp_path / "proj"
    assert dream_lock.throttle_scan(proj, 600) is False  # 首次放行
    assert dream_lock.throttle_scan(proj, 600) is True  # 紧接被节流
    assert dream_lock.throttle_scan(proj, 0) is False  # 间隔 0 不节流

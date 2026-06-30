"""Dream 锁 / lastAt / 并发标记 / 扫描节流 纯函数测试。"""

from __future__ import annotations

from lumi.agents.memory import dream_lock
from lumi.agents.memory import paths as memory_paths


def test_lastat_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    assert dream_lock.read_last_at(proj) == 0.0  # 无锁
    dream_lock.touch_lock(proj)
    assert dream_lock.read_last_at(proj) > 0  # 落锁后 mtime > 0


def test_in_flight(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj-inflight"
    assert not dream_lock.is_in_flight(proj)
    dream_lock.mark_in_flight(proj)
    assert dream_lock.is_in_flight(proj)
    dream_lock.clear_in_flight(proj)
    assert not dream_lock.is_in_flight(proj)


def test_throttle_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj-throttle"
    assert dream_lock.throttle_scan(proj, 600) is False  # 首次放行
    assert dream_lock.throttle_scan(proj, 600) is True  # 紧接着被节流
    assert dream_lock.throttle_scan(proj, 0) is False  # 间隔 0 不节流


def test_per_project_isolation(tmp_path, monkeypatch):
    """两个 project 的 in_flight 互不影响（per-project key）。"""
    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    a, b = tmp_path / "a", tmp_path / "b"
    dream_lock.mark_in_flight(a)
    assert dream_lock.is_in_flight(a) and not dream_lock.is_in_flight(b)
    dream_lock.clear_in_flight(a)

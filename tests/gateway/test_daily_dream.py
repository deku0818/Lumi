"""每日 dream 循环测试：下次触发时间计算 + 单会话判活（ts vs 上次快照时刻）。"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from lumi.agents.memory import dream_lock
from lumi.gateway.channels.feishu import daily_dream
from lumi.gateway.channels.feishu.daily_dream import _dream_one, seconds_until_next
from lumi.utils.constants import LUMI_META_KEY


def test_target_later_same_day():
    now = datetime(2026, 7, 5, 3, 0, 0)
    assert seconds_until_next(now, "04:30") == 90 * 60


def test_target_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 7, 5, 5, 0, 0)
    # 今天 03:00 已过 → 明天 03:00 = 22 小时
    assert seconds_until_next(now, "03:00") == 22 * 3600


def test_target_equal_now_rolls_forward_full_day():
    now = datetime(2026, 7, 5, 3, 0, 0)
    # target <= now 时顺延次日，避免同分钟内重复触发
    assert seconds_until_next(now, "03:00") == 24 * 3600


def test_ignores_seconds_and_micros_in_now():
    now = datetime(2026, 7, 5, 3, 0, 30, 500)
    # 目标秒/微秒清零，03:01 距 03:00:30.0005 = 29.9995s
    assert seconds_until_next(now, "03:01") == 60 - 30.0005


# --- _dream_one 判活：真实 human 落库 ts vs 该 thread 上次 dream 快照时刻 ---


def _bridge(messages: list):
    async def snapshot_messages():
        return messages

    return SimpleNamespace(workspace_dir="/proj", snapshot_messages=snapshot_messages)


def _human(ts_ms: int) -> HumanMessage:
    return HumanMessage("嗨", additional_kwargs={LUMI_META_KEY: {"ts": ts_ms}})


async def test_dream_one_skips_without_new_messages(monkeypatch):
    """最新 human ts 不晚于上次快照时刻 → 跳过；compact 后消息变少也不误触发。"""
    monkeypatch.setattr(dream_lock, "read_thread_dreamed_at", lambda p, t: 2000.0)
    called = []
    monkeypatch.setattr(
        daily_dream, "consolidate_session_dream", lambda *a, **k: called.append(a)
    )
    assert await _dream_one(_bridge([_human(1_500_000)]), "feishu-c1") is False
    assert await _dream_one(_bridge([HumanMessage("载体无 ts")]), "feishu-c1") is False
    assert not called


async def test_dream_one_runs_and_records_snapshot_ts(monkeypatch):
    """有新消息且综合成功（快照时刻已推进）→ True，快照时刻取自快照当刻。"""
    recorded: dict[str, float] = {}
    monkeypatch.setattr(
        dream_lock, "read_thread_dreamed_at", lambda p, t: recorded.get(t, 0.0)
    )
    called = []

    async def fake_consolidate(
        project_dir, messages, thread_id, snapshot_ts, *, notify
    ):
        recorded[thread_id] = snapshot_ts  # 综合成功 → 记账（同真实 record 语义）
        called.append((project_dir, thread_id, snapshot_ts, notify))

    monkeypatch.setattr(daily_dream, "consolidate_session_dream", fake_consolidate)
    before = time.time()
    assert await _dream_one(_bridge([_human(int(before * 1000))]), "feishu-c2") is True
    project_dir, thread_id, snapshot_ts, notify = called[0]
    assert project_dir == Path("/proj") and thread_id == "feishu-c2"
    assert before <= snapshot_ts <= time.time() and notify is False


async def test_dream_one_failed_dream_blocks_summary(monkeypatch):
    """综合失败（bg-task 吞异常、快照时刻未推进）→ False，把关 summary 不压未沉淀历史。"""
    monkeypatch.setattr(dream_lock, "read_thread_dreamed_at", lambda p, t: 0.0)

    async def failed_consolidate(*a, **k):
        pass  # 失败路径：正常返回但没有记账（run_background_task 吞掉了异常）

    monkeypatch.setattr(daily_dream, "consolidate_session_dream", failed_consolidate)
    msgs = [_human(int(time.time() * 1000))]
    assert await _dream_one(_bridge(msgs), "feishu-c3") is False

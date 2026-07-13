"""/goal 命令层：clear / 裸回显两条纯 UI 路径写 sidecar 并回执。

激活路径（跑真实一轮）需真实 agent，留给端到端；此处只验命令分派与 sidecar 副作用。
"""

from __future__ import annotations

import pytest

from lumi.gateway.bridge import AgentBridge
from lumi.gateway.bridge.core import EventKind, available_commands
from lumi.sessions import session_meta

THREAD = "t-goal-cmd"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    b = AgentBridge()
    b._config = {"configurable": {"thread_id": THREAD}}
    return b


async def _drain(gen):
    return [e async for e in gen]


def test_goal_in_available_commands():
    names = [c["name"] for c in available_commands(memory_enabled=False)]
    assert "goal" in names


async def test_clear_removes_goal_and_replies(bridge):
    session_meta.update_meta(THREAD, goal="建 hello.txt")
    events = await _drain(bridge._stream_goal_command("clear", "default", None))

    assert session_meta.get_goal(THREAD) == ""
    texts = [e.text for e in events if e.kind == EventKind.MESSAGE_DELTA]
    assert any("解除" in t for t in texts)


async def test_bare_echoes_current_goal(bridge):
    session_meta.update_meta(THREAD, goal="建 hello.txt")
    events = await _drain(bridge._stream_goal_command("", "default", None))

    texts = [e.text for e in events if e.kind == EventKind.MESSAGE_DELTA]
    assert any("建 hello.txt" in t for t in texts)
    assert session_meta.get_goal(THREAD) == "建 hello.txt"  # 裸回显不改条件


async def test_bare_no_goal_shows_usage(bridge):
    events = await _drain(bridge._stream_goal_command("", "default", None))
    texts = [e.text for e in events if e.kind == EventKind.MESSAGE_DELTA]
    assert any("/goal" in t for t in texts)

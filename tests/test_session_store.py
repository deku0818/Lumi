"""session_store 派生逻辑：压缩后无首条 human 的会话不应从列表消失。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from lumi.sessions.session_store import _summary_from_snapshot


def _snap(messages: list):
    return SimpleNamespace(
        values={"messages": messages},
        created_at="2026-07-05T00:00:00+00:00",
        metadata={"workspace_dir": "/proj"},
    )


def test_compacted_session_without_first_human_still_listed():
    # 压缩后只剩摘要载体 + 末条 AI（首条真实 human 已并入摘要）——仍是有内容的会话
    msgs = [
        HumanMessage(content="<summary>\n往期摘要\n</summary>\n", id="sum"),
        AIMessage(content="最近的回复", id="a"),
    ]
    summary = _summary_from_snapshot("t1", _snap(msgs))
    assert summary is not None  # 不再被丢弃
    assert summary.first_message == ""  # 取不到首条 human → 留空，标题交上层 meta 兜
    assert summary.message_count == 2


def test_normal_session_keeps_first_message():
    msgs = [HumanMessage(content="第一个问题", id="h"), AIMessage(content="答", id="a")]
    summary = _summary_from_snapshot("t2", _snap(msgs))
    assert summary is not None
    assert summary.first_message == "第一个问题"


def test_empty_snapshot_returns_none():
    assert _summary_from_snapshot("t3", _snap([])) is None
    assert _summary_from_snapshot("t4", SimpleNamespace(values=None)) is None

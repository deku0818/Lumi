"""list_sessions RPC：置顶会话不受 limit 截断。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from lumi.gateway.session import _list_sessions
from lumi.sessions import session_meta


async def test_pinned_session_beyond_limit_still_listed(tmp_path, monkeypatch):
    # 回归：先按 limit 截断再把置顶排前，落在最近 limit 条之外的置顶会话从侧栏消失
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        builder = StateGraph(MessagesState)
        builder.add_node("noop", lambda state: {})
        builder.add_edge(START, "noop")
        builder.add_edge("noop", END)
        graph = builder.compile(checkpointer=saver)
        for tid in ("t-old", "t-mid", "t-new1", "t-new2"):  # 由旧到新
            await graph.ainvoke(
                {"messages": [HumanMessage(content=tid, id=tid)]},
                {"configurable": {"thread_id": tid}},
            )
        session_meta.update_meta("t-old", pinned=True)

        result = await _list_sessions(SimpleNamespace(graph=graph), {"limit": 2})

    ids = [s["thread_id"] for s in result["sessions"]]
    assert ids == ["t-old", "t-new2", "t-new1"]  # 置顶在前，其余按最近活跃

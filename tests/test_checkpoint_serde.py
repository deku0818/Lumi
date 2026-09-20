"""checkpoint 加密落盘（可选）与节点 trace 输入摘要。

两件事都是「默认不开 / 开了不许影响执行」，所以都从**可观察结果**上锁：
加密看盘上还能不能搜到原文，trace 看节点拿到的 state 有没有被改。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import graph_run_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from lumi.agents.core import graph as graph_module
from lumi.agents.core import nodes
from lumi.agents.core.graph import (
    CHECKPOINT_AES_KEY_ENV,
    LumiAgent,
    _checkpoint_serde,
    _digest_history,
)
from lumi.agents.core.node_helpers.messages import stamp_missing_ids
from lumi.agents.core.state import LumiAgentContext

_SECRET = "绝密内容-XYZZY"


def test_serde_is_plain_but_allowlisted_without_key(monkeypatch):
    """默认明文（不引入加密依赖），但类型白名单恒生效。"""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    serde = _checkpoint_serde()
    assert type(serde) is JsonPlusSerializer
    assert (
        "lumi.agents.tools.providers.todo",
        "Todo",
    ) in serde._allowed_msgpack_modules


def test_serde_encrypts_with_key_and_keeps_allowlist(monkeypatch):
    monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    serde = _checkpoint_serde()
    assert type(serde).__name__ == "EncryptedSerializer"
    # 加密只是外层包装，内层仍是带白名单的 JsonPlusSerializer
    assert (
        "lumi.agents.tools.providers.todo",
        "Todo",
    ) in serde.serde._allowed_msgpack_modules


@pytest.mark.asyncio
async def test_encrypted_checkpoint_hides_plaintext_on_disk(tmp_path, monkeypatch):
    """开了密钥后盘上搜不到原文；同一密钥读得回来。库落在 tmp_path，不碰 ~/.lumi。"""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    db = tmp_path / "ck.db"
    conn = await aiosqlite.connect(str(db))
    saver = AsyncSqliteSaver(conn, serde=_checkpoint_serde())
    await saver.setup()
    graph = LumiAgent(checkpointer=saver).graph
    config = {"configurable": {"thread_id": "enc"}}
    await graph.aupdate_state(
        config,
        stamp_missing_ids({"messages": [HumanMessage(_SECRET)]}),
        as_node="OfflineFlush",
    )
    readback = [m.content for m in (await graph.aget_state(config)).values["messages"]]
    await conn.close()

    assert readback == [_SECRET]
    assert _SECRET.encode() not in db.read_bytes()


def test_digest_history_replaces_messages_only():
    digested = _digest_history({"messages": [1, 2, 3], "iterations": 7})
    assert digested["messages"] == "<3 条历史，trace 已省略>"
    assert digested["iterations"] == 7  # 其余键原样
    assert _digest_history("不是 dict") == "不是 dict"


@pytest.mark.asyncio
async def test_trace_digest_does_not_touch_what_the_node_receives():
    """trace_policy 只改**记录**的内容——节点拿到的仍是完整历史。"""
    seen = {}

    async def spy(state, runtime):
        seen["count"] = len(state["messages"])
        return {"messages": [AIMessage("ok", id="r1")], "iterations": 2}

    history = [HumanMessage(f"m{i}", id=f"h{i}") for i in range(12)]
    traced: list = []
    with (
        patch.object(nodes, "get_config", return_value=graph_run_config()),
        patch.object(graph_module, "call_model", spy),
    ):
        graph = LumiAgent(checkpointer=MemorySaver()).graph
        async for event in graph.astream_events(
            {"messages": history, "iterations": 1},
            {"configurable": {"thread_id": "t-trace"}},
            context=LumiAgentContext(model_name="fake"),
            version="v2",
        ):
            if event["event"] == "on_chain_start" and event.get("name") == "CallModel":
                traced.append(event["data"]["input"]["messages"])

    assert traced == ["<12 条历史，trace 已省略>"]
    assert seen["count"] == 12


@pytest.mark.asyncio
async def test_todo_survives_checkpoint_roundtrip_without_warning(tmp_path, caplog):
    """`Todo` 进 checkpoint 再读回来仍是 `Todo`，且不刷「未注册类型」告警。

    langgraph 默认对未注册类型「警告但放行」，并预告 will be blocked in a future
    version——真 block 那天 todos 会静默变空。显式注册后转严格模式，本用例同时
    锁住「注册了」和「读得回来」：把 `_ALLOWED_CHECKPOINT_TYPES` 里的 Todo 拿掉，
    它会因为被拦截而红。
    """
    import logging

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from lumi.agents.tools.providers.todo import Todo

    conn = await aiosqlite.connect(str(tmp_path / "todo.db"))
    saver = AsyncSqliteSaver(conn, serde=_checkpoint_serde())
    await saver.setup()
    graph = LumiAgent(checkpointer=saver).graph
    config = {"configurable": {"thread_id": "todo"}}

    with caplog.at_level(logging.WARNING):
        await graph.aupdate_state(
            config,
            {"todos": [Todo(content="写测试", status="pending")]},
            as_node="OfflineFlush",
        )
        todos = (await graph.aget_state(config)).values["todos"]
    await conn.close()

    assert [type(t).__name__ for t in todos] == ["Todo"]
    assert todos[0].content == "写测试" and todos[0].status == "pending"
    noisy = [r.getMessage() for r in caplog.records if "eserializ" in r.getMessage()]
    assert not noisy, noisy

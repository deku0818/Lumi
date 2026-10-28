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
from lumi.agents.core.state import LumiAgentContext

_SECRET = "绝密内容-XYZZY"


def test_serde_is_plain_but_allowlisted_without_key(monkeypatch):
    """默认明文（不引入加密依赖），但类型白名单恒生效。"""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    monkeypatch.delenv(CHECKPOINT_AES_KEY_ENV, raising=False)
    serde = _checkpoint_serde()
    assert type(serde) is JsonPlusSerializer
    assert serde._allowed_msgpack_modules is None  # 仅 SDK 内置安全类型


def test_serde_encrypts_with_key_and_keeps_allowlist(monkeypatch):
    monkeypatch.setenv(CHECKPOINT_AES_KEY_ENV, "0123456789abcdef")
    serde = _checkpoint_serde()
    assert type(serde).__name__ == "EncryptedSerializer"
    # 加密只是外层包装，内层仍是带白名单的 JsonPlusSerializer
    assert serde.serde._allowed_msgpack_modules is None


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
        {"messages": [HumanMessage(_SECRET)]},
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
        return {"messages": [AIMessage("ok", id="r1")]}

    history = [HumanMessage(f"m{i}", id=f"h{i}") for i in range(12)]
    traced: list = []
    with (
        patch.object(nodes, "get_config", return_value=graph_run_config()),
        patch.object(graph_module, "call_model", spy),
    ):
        graph = LumiAgent(checkpointer=MemorySaver()).graph
        async for event in graph.astream_events(
            {"messages": history},
            {"configurable": {"thread_id": "t-trace"}},
            context=LumiAgentContext(model_name="fake"),
            version="v2",
        ):
            if event["event"] == "on_chain_start" and event.get("name") == "CallModel":
                traced.append(event["data"]["input"]["messages"])

    assert traced == ["<12 条历史，trace 已省略>"]
    assert seen["count"] == 12

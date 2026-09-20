"""网络瞬态错误重试时，用的必须是**当前**的 config，不能钉住上一次的 checkpoint_id。

回归：``stream_config`` 曾在重试循环外只快照一次。rewind / resume 会往 ``_config``
钉 ``checkpoint_id``，每次流结束的 finally 再把它 pop 掉；快照一次的话重试仍带着
那个旧 id，LangGraph 会从旧 checkpoint 重放第 0 次已落库的工作。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain_core.messages import HumanMessage
from toy_graph import bridge_with, build_graph

from lumi.gateway.protocol import EventKind


@pytest.mark.asyncio
async def test_retry_uses_current_config_without_pinned_checkpoint_id(monkeypatch):
    graph = build_graph(asyncio.Event(), ["reply", "reply"])
    config = {"configurable": {"thread_id": "t-retry", "checkpoint_id": "cp-PINNED"}}
    bridge = bridge_with(config, graph)

    seen_checkpoint_ids: list = []
    real_astream_events = graph.astream_events
    attempts = {"n": 0}

    def spy(input_data, cfg, **kwargs):
        attempts["n"] += 1
        seen_checkpoint_ids.append(cfg["configurable"].get("checkpoint_id"))
        if attempts["n"] == 1:
            # 第 0 次跑到一半掉线：走 bridge 的 MAX_STREAM_RETRIES 分支
            async def boom():
                raise httpx.ReadError("connection reset")
                yield  # pragma: no cover

            return boom()
        return real_astream_events(input_data, cfg, **kwargs)

    monkeypatch.setattr(graph, "astream_events", spy)
    monkeypatch.setattr("lumi.gateway.bridge.core.RETRY_BASE_WAIT", 0)

    kinds = [e.kind async for e in bridge._stream({"messages": [HumanMessage("你好")]})]

    assert attempts["n"] == 2, "应当重试一次"
    assert seen_checkpoint_ids[0] == "cp-PINNED", "第 0 次带着 rewind 钉的 checkpoint"
    assert seen_checkpoint_ids[1] is None, (
        "重试必须用 pop 之后的 config，否则会从旧 checkpoint 重放已落库的工作"
    )
    # 重试确实以「从 checkpoint 续跑」的形态发起（input=None）；玩具图里第 0 次
    # 掉线前没写过 checkpoint，续跑自然无从恢复，故此处不断言最终事件
    assert EventKind.MESSAGE_DELTA in kinds  # 重试提示已发给用户

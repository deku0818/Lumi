"""ApprovalBroker 单测：request 挂起 → resolve/reject_all 唤醒，并发 approval_id 互不串扰。

adispatch_custom_event 只能在 run 上下文内调用，故经 RunnableLambda.astream_events
驱动 broker.request——这同时忠实覆盖了「dispatch → on_custom_event 浮现 → 会话层
resolve」的真实闭环。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.runnables import RunnableLambda

from lumi.gateway.bridge.broker import LUMI_APPROVAL_EVENT, ApprovalBroker

_REJECT = {"decision": "reject"}


async def test_request_resolves_with_decision():
    """request 挂起后被 resolve 唤醒，返回会话层喂入的 decision，registry 清空。"""
    broker = ApprovalBroker()
    holder: dict = {}

    async def node(_):
        holder["result"] = await broker.request(
            {"type": "tool_approval", "tool_calls": []}, _REJECT
        )

    chain = RunnableLambda(node)
    async for ev in chain.astream_events("x"):
        if ev["event"] == "on_custom_event" and ev["name"] == LUMI_APPROVAL_EVENT:
            assert ev["data"]["approval_id"]  # broker 注入了 approval_id
            assert broker.resolve(ev["data"]["approval_id"], {"decision": "approve"})

    assert holder["result"] == {"decision": "approve"}
    assert broker._pending == {}  # finally 清理


async def test_concurrent_requests_do_not_crosstalk():
    """同一轮内多个 approval_id 并发挂起，各自按 id 解析，互不串扰。"""
    broker = ApprovalBroker()
    holder: dict = {}

    async def node(_):
        holder["results"] = await asyncio.gather(
            broker.request({"type": "ask", "n": 1}, _REJECT),
            broker.request({"type": "ask", "n": 2}, _REJECT),
        )

    chain = RunnableLambda(node)
    async for ev in chain.astream_events("x"):
        if ev["event"] == "on_custom_event" and ev["name"] == LUMI_APPROVAL_EVENT:
            d = ev["data"]
            broker.resolve(d["approval_id"], f"answer-{d['n']}")

    assert holder["results"] == ["answer-1", "answer-2"]
    assert broker._pending == {}


async def test_reject_all_resolves_with_each_reject_value():
    """reject_all 把每个挂起请求按各自 reject_value 收尾（非取消），返回处理数。"""
    broker = ApprovalBroker()
    holder: dict = {}

    async def node(_):
        holder["results"] = await asyncio.gather(
            broker.request(
                {"type": "tool_approval", "n": 1}, {"decision": "reject", "n": 1}
            ),
            broker.request({"type": "ask", "n": 2}, "__ask_cancelled__"),
        )

    chain = RunnableLambda(node)
    rejected = []
    async for ev in chain.astream_events("x"):
        if ev["event"] == "on_custom_event" and ev["name"] == LUMI_APPROVAL_EVENT:
            # 两个请求都登记后一次性按各自 reject_value 收尾（仅调用一次）
            if len(broker._pending) == 2 and not rejected:
                rejected.append(broker.reject_all())

    assert rejected == [2]
    assert holder["results"] == [{"decision": "reject", "n": 1}, "__ask_cancelled__"]
    assert broker._pending == {}


async def test_reject_all_empty_returns_zero():
    """无挂起请求时 reject_all 返回 0（调用方据此回退到硬取消）。"""
    broker = ApprovalBroker()
    assert broker.reject_all() == 0


def test_resolve_unknown_id_returns_false():
    """对未知 / 已决 approval_id resolve 返回 False，不抛错。"""
    broker = ApprovalBroker()
    assert broker.resolve("nope", {"decision": "approve"}) is False


async def test_task_cancel_during_request_tears_down_cleanly():
    """端到端：节点经 broker 挂起时取消跑 astream_events 的外层 task（= 流生成中途 stop /
    连接断开），取消干净冒泡、broker registry 清空、不泄漏挂起 Future。

    锁住在途审批最关键的运行时假设：外层 task 取消传播到挂在 broker 上的节点。
    """
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    broker = ApprovalBroker()

    class _S(TypedDict):
        x: int

    async def parked(state):
        # 同 human_approval：await broker.request，不捕获 CancelledError
        await broker.request({"type": "tool_approval", "tool_calls": []}, _REJECT)
        return {"x": 1}

    g = StateGraph(_S)
    g.add_node("parked", parked)
    g.add_edge(START, "parked")
    g.add_edge("parked", END)
    graph = g.compile()

    seen: list[str] = []

    async def consumer():
        async for ev in graph.astream_events({"x": 0}, version="v2"):
            if ev["event"] == "on_custom_event" and ev["name"] == LUMI_APPROVAL_EVENT:
                seen.append("approval")

    task = asyncio.create_task(consumer())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if seen:
            break
    assert seen == ["approval"]
    assert len(broker._pending) == 1

    # 模拟流生成中途 stop / 连接断开：取消外层 task（无挂起审批可拒时的硬取消路径）
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert broker._pending == {}  # 无泄漏

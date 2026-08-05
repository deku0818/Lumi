"""persist_partial_reply / finalize_cancelled_stream 特征测试。

中断时把已流出的半截回复写回 checkpoint：CallModel 未返回时那条 AIMessage 从未
进 state——stop 硬取消后前端显示过的半截回复在下一轮凭空消失。session/channel
的取消分支经 finalize_cancelled_stream（先 aclose 图再写回）调用。
防写重按消息 id，id 缺失退回 extract_text_content 文本判重（str/block-list 通吃）。
玩具图脚手架见 toy_graph.py；真实图行为由 test_offline_flush_contract 锁定。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from toy_graph import bridge_with, build_graph, run_turn, run_turn_and_cancel

from lumi.gateway.bridge import AgentBridge
from lumi.gateway.protocol import EventKind


@pytest.mark.asyncio
async def test_partial_reply_persisted():
    """半截回复作为带 interrupted 标记的 AIMessage 落库，buffer 清空。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["tool", "hang"])
    config = {"configurable": {"thread_id": "t1"}}
    await run_turn_and_cancel(graph, config, "第一轮")

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["我看完了子agent的结果，", "结论是"]
    bridge._partial_msg_id = "msg-live-1"
    await bridge.persist_partial_reply()

    assert bridge._partial_chunks == []
    state = await graph.aget_state(config)
    tail = state.values["messages"][-1]
    assert isinstance(tail, AIMessage)
    assert tail.content == "我看完了子agent的结果，结论是"
    assert tail.id == "msg-live-1"
    assert tail.additional_kwargs["lumi"]["interrupted"] is True


@pytest.mark.asyncio
async def test_dedup_by_message_id():
    """cancel 落在节点返回落库之后：同 id 消息已在历史里，不写重。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply"])
    config = {"configurable": {"thread_id": "t2"}}
    await run_turn(graph, config, "第一轮")

    state = await graph.aget_state(config)
    committed_id = state.values["messages"][-1].id
    before = len(state.values["messages"])

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["回复"]
    bridge._partial_msg_id = committed_id
    await bridge.persist_partial_reply()

    after = len((await graph.aget_state(config)).values["messages"])
    assert after == before


@pytest.mark.asyncio
async def test_dedup_by_text_with_block_content():
    """id 缺失时退回文本判重，block-list content + 多行文本也认得出。

    旧实现 `text in str(last.content)` 对 list content 做 repr（换行被转义），
    多行必失配写重——此测试正是那个形态。
    """
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply"])
    config = {"configurable": {"thread_id": "t3"}}
    await run_turn(graph, config, "第一轮")
    # 追加一条 block-list content 的多行 AIMessage（Anthropic 形态）
    await graph.aupdate_state(
        config,
        {"messages": [AIMessage(content=[{"type": "text", "text": "第一行\n第二行"}])]},
        as_node="OfflineFlush",
    )
    before = len((await graph.aget_state(config)).values["messages"])

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["第一行\n", "第二行"]
    bridge._partial_msg_id = ""  # 模拟 provider 不给 id
    await bridge.persist_partial_reply()

    after = len((await graph.aget_state(config)).values["messages"])
    assert after == before


@pytest.mark.asyncio
async def test_empty_buffer_noop():
    bridge = AgentBridge()
    await bridge.persist_partial_reply()  # 空 buffer 直接返回，不碰任何依赖


@pytest.mark.asyncio
async def test_stream_accumulates_main_chain_text():
    """贯穿 _stream 的接线：主链 CallModel 的流式正文逐 token 攒进 buffer，
    中途 cancel 后 buffer 里正是已流出的前缀，persist 即可落库。"""
    full_text = "星夜长谈未完"
    model = FakeListChatModel(responses=[full_text], sleep=0.01)  # 逐字符流式

    async def call_model(state):
        reply = await model.ainvoke(state["messages"])
        return {"messages": [reply]}

    graph = build_graph(asyncio.Event(), [], call_model_fn=call_model)

    config = {"configurable": {"thread_id": "t5"}}
    bridge = bridge_with(config, graph)

    received: list[str] = []
    got_three = asyncio.Event()

    async def consume():
        async for evt in bridge._stream({"messages": [HumanMessage("你好")]}):
            if evt.kind == EventKind.MESSAGE_DELTA:
                received.append(evt.text)
                if len(received) >= 3:
                    got_three.set()

    task = asyncio.create_task(consume())
    await got_three.wait()  # 按计数取消，不赌时序
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # buffer 与前端收到的一致（最后一个 token 可能已攒未 yield，故是前缀关系）
    assert "".join(bridge._partial_chunks).startswith("".join(received))
    assert "".join(bridge._partial_chunks) != full_text  # 确在中途，不是整段

    await bridge.persist_partial_reply()
    state = await graph.aget_state(config)
    tail = state.values["messages"][-1]
    assert isinstance(tail, AIMessage)
    assert tail.additional_kwargs["lumi"]["interrupted"] is True


@pytest.mark.asyncio
async def test_persist_pairs_dangling_before_partial():
    """cancel 落在工具执行期间：同一次写入先补配对 ToolMessage 再接半截——
    顺序对 API 合法，且修复随写入完成、不依赖下一轮。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["tool"], tool_hangs=True)
    config = {"configurable": {"thread_id": "t7"}}
    await run_turn_and_cancel(graph, config, "第一轮")

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["先说两句"]
    bridge._partial_msg_id = "msg-x"
    await bridge.persist_partial_reply()

    state = await graph.aget_state(config)
    msgs = state.values["messages"]
    assert isinstance(msgs[-2], ToolMessage) and msgs[-2].tool_call_id == "tc1"
    assert "核实" in msgs[-2].content  # 措辞不断言未执行
    assert isinstance(msgs[-1], AIMessage) and msgs[-1].content == "先说两句"
    assert state.next == ()  # OfflineFlush 出边直达 END：写完即干净

    # 修复已随写入完成，_recover 不再补第二份
    await bridge._recover_stale_state(graph)
    msgs2 = (await graph.aget_state(config)).values["messages"]
    assert sum(isinstance(m, ToolMessage) for m in msgs2) == 1


@pytest.mark.asyncio
async def test_finalize_closes_gen_then_persists():
    """finalize_cancelled_stream：先关生成器再写回，一次调用完成整套收尾。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["tool", "hang"])
    config = {"configurable": {"thread_id": "t6"}}
    await run_turn_and_cancel(graph, config, "第一轮")

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["半截"]

    closed = {"v": False}

    async def fake_gen():
        try:
            yield None
        finally:
            closed["v"] = True

    gen = fake_gen()
    await gen.__anext__()  # 停在 yield 上（模拟取消落在消费侧 send）
    await bridge.finalize_cancelled_stream(gen)

    assert closed["v"] is True  # 生成器被确定性关闭
    tail = (await graph.aget_state(config)).values["messages"][-1]
    assert tail.content == "半截"


@pytest.mark.asyncio
async def test_composes_with_stale_recovery_and_next_turn():
    """与 stale 修复拼合：写回半截 → 就地修复 → 下一轮续跑，历史三段俱全。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["tool", "hang", "reply"])
    config = {"configurable": {"thread_id": "t4"}}
    await run_turn_and_cancel(graph, config, "第一轮")

    bridge = bridge_with(config, graph)
    bridge._partial_chunks = ["半截结论"]
    await bridge.persist_partial_reply()
    await bridge._recover_stale_state(graph)

    gate.set()
    await run_turn(graph, config, "第二轮")

    state = await graph.aget_state(config)
    assert not state.next
    contents = [m.content for m in state.values["messages"]]
    assert "子agent结果" in contents  # 工具结果
    assert "半截结论" in contents  # 半截回复
    assert "第二轮" in contents

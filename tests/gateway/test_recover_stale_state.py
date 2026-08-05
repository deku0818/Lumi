"""_recover_stale_state 就地修复特征测试。

中断（stop 硬取消）留下的 stale checkpoint（next 非空、无 interrupt）不再回退
到轮前干净 checkpoint——那会丢掉已落库的工具结果（如子 agent 的产出）。新行为：

- 尾部合法（工具已执行、ToolMessage 已配对）→ 完全不动，直接从最新 checkpoint 续跑；
- 尾部悬空（AIMessage 挂着未应答 tool_calls）→ 补合成 ToolMessage 收干净；
- ptl_retry 残留（中断落在 PTL 强制压缩期间）→ 就地清掉，防下一轮无条件有损压缩。

注意中断轮之前必须先跑完一个正常轮：旧回退逻辑的目标（END 处干净 checkpoint）
存在时才会触发丢历史，单轮线程会歪打正着走"找不到就 pop"分支——测试须贴真实场景。
玩具图脚手架见 toy_graph.py；不初始化真实 Agent graph。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from toy_graph import bridge_with, build_graph, run_turn, run_turn_and_cancel


@pytest.mark.asyncio
async def test_tool_result_preserved_no_rollback():
    """前轮已完成 + 工具已执行回复中被断：不回退、不动 config，工具结果保住。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply", "tool", "hang"])
    config = {"configurable": {"thread_id": "t1"}}
    await run_turn(graph, config, "第一轮")  # 完成轮：END 处留下干净 checkpoint
    await run_turn_and_cancel(graph, config, "第二轮")

    state = await graph.aget_state(config)
    assert state.next  # 确实 stale
    assert isinstance(state.values["messages"][-1], ToolMessage)

    await bridge_with(config)._recover_stale_state(graph)

    # 旧行为会把 checkpoint_id 指回第一轮 END 的干净点；新行为完全不动
    assert "checkpoint_id" not in config["configurable"]
    state = await graph.aget_state(config)
    contents = [m.content for m in state.values["messages"]]
    assert "子agent结果" in contents  # 工具结果没有被回退丢弃
    assert "第二轮" in contents  # 中断轮的用户消息也在
    # 尾部已配对，无需也不应补合成 ToolMessage
    assert sum(isinstance(m, ToolMessage) for m in state.values["messages"]) == 1


@pytest.mark.asyncio
async def test_dangling_tool_call_gets_synthetic_result():
    """工具未执行完就被断：给悬空 tool_call 补合成 ToolMessage 收干净。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply", "tool"], tool_hangs=True)
    config = {"configurable": {"thread_id": "t2"}}
    await run_turn(graph, config, "第一轮")
    await run_turn_and_cancel(graph, config, "第二轮")

    state = await graph.aget_state(config)
    assert state.next
    last = state.values["messages"][-1]
    assert isinstance(last, AIMessage) and last.tool_calls  # tool_use 悬空

    await bridge_with(config)._recover_stale_state(graph)

    state = await graph.aget_state(config)
    tail = state.values["messages"][-1]
    assert isinstance(tail, ToolMessage)
    assert tail.tool_call_id == "tc1"
    # 措辞不断言"未执行"——取消可能落在工具已完成但 checkpoint 未提交的窗口
    assert "中断" in tail.content and "未执行" not in tail.content
    contents = [m.content for m in state.values["messages"]]
    assert "第二轮" in contents  # 中断轮的用户消息保住


@pytest.mark.asyncio
async def test_ptl_retry_flag_cleared():
    """中断落在 PTL 强制压缩期间：残留的 ptl_retry 被就地清掉。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply", "tool"], tool_hangs=True, ptl=True)
    config = {"configurable": {"thread_id": "t5"}}
    await run_turn(graph, config, "第一轮")
    await run_turn_and_cancel(graph, config, "第二轮")

    state = await graph.aget_state(config)
    assert state.values.get("ptl_retry") is True  # flag 确实残留

    await bridge_with(config)._recover_stale_state(graph)

    state = await graph.aget_state(config)
    assert state.values.get("ptl_retry") is False


@pytest.mark.asyncio
async def test_clean_state_untouched():
    """正常跑完的轮：no-op。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply"])
    config = {"configurable": {"thread_id": "t3"}}
    await run_turn(graph, config, "第一轮")

    before = await graph.aget_state(config)
    assert not before.next

    await bridge_with(config)._recover_stale_state(graph)

    after = await graph.aget_state(config)
    assert after.values["messages"] == before.values["messages"]


@pytest.mark.asyncio
async def test_next_turn_continues_with_full_history():
    """修复后带新输入续跑：中断轮的用户消息 + 工具结果全在历史里。"""
    gate = asyncio.Event()
    graph = build_graph(gate, ["reply", "tool", "hang", "reply"])
    config = {"configurable": {"thread_id": "t4"}}
    await run_turn(graph, config, "第一轮")
    await run_turn_and_cancel(graph, config, "第二轮")
    await bridge_with(config)._recover_stale_state(graph)

    gate.set()  # 防御：若误从 stale 任务恢复也不至于挂死
    await run_turn(graph, config, "第三轮")

    state = await graph.aget_state(config)
    assert not state.next
    contents = [m.content for m in state.values["messages"]]
    assert "第二轮" in contents
    assert "子agent结果" in contents
    assert "第三轮" in contents

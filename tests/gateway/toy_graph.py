"""中断收尾测试共用的玩具图脚手架（test_recover_stale_state / test_persist_partial_reply）。

与 Lumi 主链同名节点（CallModel/ToolExecutor/OfflineFlush），用真实 cancel 制造
stale checkpoint。注意玩具图锁不住真实图的条件边形状（as_node 事故的教训），
真实图行为由 test_offline_flush_contract 锁定。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from lumi.gateway.bridge import AgentBridge


class ToyState(MessagesState):
    ptl_retry: bool


def build_graph(
    gate: asyncio.Event,
    model_script: list[str],
    tool_hangs: bool = False,
    ptl: bool = False,
    call_model_fn=None,
):
    """START → CallModel → ToolExecutor → CallModel → END 的玩具图，返回已编译图。

    model_script 按次驱动 CallModel：'tool' 发 tool_call、'hang' 在 gate 上挂住
    （供测试 cancel）、'reply' 正常回复。tool_hangs=True 时 ToolExecutor 挂住
    （模拟工具未执行完就被断）；ptl=True 时 'tool' 步同时置 ptl_retry=True
    （模拟中断落在 PTL 强制压缩期间的 flag 残留）。call_model_fn 可整体替换
    CallModel 节点实现（如接真实流式 fake 模型），其余拓扑不变。

    挂住的节点在进入 gate.wait 前 set 编译图上挂着的 ``entered`` 事件，
    run_turn_and_cancel 据此事件驱动地取消，不靠 sleep 赌时序。
    """
    entered = asyncio.Event()

    async def call_model(state: ToyState):
        action = model_script.pop(0)
        if action == "tool":
            update: dict = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "agent", "args": {}, "id": "tc1"}],
                    )
                ]
            }
            if ptl:
                update["ptl_retry"] = True
            return update
        if action == "hang":
            entered.set()
            await gate.wait()
        return {"messages": [AIMessage(content="回复")]}

    async def tool_executor(state: ToyState):
        if tool_hangs:
            entered.set()
            await gate.wait()
        return {"messages": [ToolMessage(content="子agent结果", tool_call_id="tc1")]}

    def route(state: ToyState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "ToolExecutor"
        return END

    builder = StateGraph(ToyState)
    builder.add_node("CallModel", call_model_fn or call_model)
    builder.add_node("ToolExecutor", tool_executor)
    builder.add_node("OfflineFlush", lambda _state: {})  # 离线写回锚点，同真实图
    builder.add_edge(START, "CallModel")
    builder.add_conditional_edges("CallModel", route, ["ToolExecutor", END])
    builder.add_edge("ToolExecutor", "CallModel")
    builder.add_edge("OfflineFlush", END)
    graph = builder.compile(checkpointer=MemorySaver())
    graph.entered = entered  # 供 run_turn_and_cancel 事件驱动取消
    return graph


async def run_turn(graph, config, text: str) -> None:
    async for _ in graph.astream_events(
        {"messages": [HumanMessage(content=text)]}, config, version="v2"
    ):
        pass


async def run_turn_and_cancel(graph, config, text: str) -> None:
    """跑一轮并在挂住的节点处 cancel，制造真实 stale checkpoint（事件驱动，零 sleep）。"""
    task = asyncio.create_task(run_turn(graph, config, text))
    await graph.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def bridge_with(config: dict, graph=None) -> AgentBridge:
    from types import SimpleNamespace

    bridge = AgentBridge()
    bridge._config = config
    if graph is not None:
        bridge._agent = SimpleNamespace(graph=graph)
    return bridge

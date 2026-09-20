"""停机（drain）停下的一轮对外发什么事件。

回归：``GraphDrained`` 分支 break 后曾落到 ``yield await self._turn_complete_event()``，
把一轮**没跑完**的会话报成已完成——前端据此收掉运行态，续跑时又往「已完成」的轮里
灌流。停机只该收口半截气泡（MESSAGE_COMPLETE），不该报 turn.complete。

用 toy_graph 的真实 ``_stream`` 脚手架跑，不是结构断言。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from toy_graph import bridge_with, build_graph

from lumi.agents.core.run_control import drain_all
from lumi.gateway.protocol import EventKind


@pytest.mark.asyncio
async def test_drained_turn_emits_message_complete_but_not_turn_complete():
    async def call_model(state):
        # 第一步跑完就请求停机：下一个 super-step（ToolExecutor）开跑前停下。
        # 走生产同一个入口 drain_all——_stream 已把本轮的 control 登记进去了
        drain_all("test")
        return {
            "messages": [
                AIMessage(
                    content="半截",
                    tool_calls=[{"name": "agent", "args": {}, "id": "tc1"}],
                )
            ]
        }

    graph = build_graph(asyncio.Event(), [], call_model_fn=call_model)
    config = {"configurable": {"thread_id": "t-drain-evt"}}
    bridge = bridge_with(config, graph)

    kinds = [
        event.kind
        async for event in bridge._stream({"messages": [HumanMessage("你好")]})
    ]

    assert EventKind.ERROR not in kinds, f"停机不该报错: {kinds}"
    assert EventKind.MESSAGE_COMPLETE in kinds, f"半截气泡要收口: {kinds}"
    assert EventKind.TURN_COMPLETE not in kinds, f"这一轮并没跑完: {kinds}"

    # 图确实停在边界：next 指向待执行节点，续跑传 None 即可接上
    snapshot = await graph.aget_state(config)
    assert snapshot.next, "drain 应停在 super-step 边界而非跑完"

"""workflow 子代理的构建与执行环境：与 agent 工具同一套构建，各自独立 shell。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from lumi.agents.core.state import LumiAgentContext
from lumi.agents.core.workflow.engine import WorkflowEngine
from lumi.agents.runtime.shell_session import current_shell_key


async def test_workflow_subagents_get_isolated_shell_and_parent_env(monkeypatch):
    # 回归：workflow 自己复制了一份子代理构建，漏了 run_with_shell（全部落到主会话的
    # 持久 shell——并行扇出在同一把锁上串行，cd/export 污染兄弟代理与主会话），也漏了
    # 渠道 env 传播（飞书里的 workflow 子代理不知道自己在哪个群）
    seen: list[tuple[str, str]] = []

    class _Graph:
        async def ainvoke(self, inputs, context=None):
            seen.append((current_shell_key(), context.env_extra))
            return {"messages": [AIMessage(content="ok")]}

    async def fake_get_tools(**kwargs):
        return []

    async def fake_create_agent(**kwargs):
        return SimpleNamespace(graph=_Graph()), LumiAgentContext()

    monkeypatch.setattr("lumi.agents.tools.get_tools", fake_get_tools)
    monkeypatch.setattr("lumi.agents.core.graph.create_agent", fake_create_agent)
    parent = LumiAgentContext(env_extra="  会话来源: 飞书")
    engine = WorkflowEngine(
        "export const meta = {name:'x',description:'x'}", parent=parent
    )

    await asyncio.gather(engine._agent("a"), engine._agent("b"))

    keys = [key for key, _ in seen]
    assert all(keys) and len(set(keys)) == 2  # 各自独立 shell，不落主会话（空键）
    assert [env for _, env in seen] == [parent.env_extra] * 2

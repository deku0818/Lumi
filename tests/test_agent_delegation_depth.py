"""agent 工具委派深度限制测试

覆盖：
- _child_tools 纯函数门控（按 child_depth/max_depth 决定是否保留 agent 工具）
- 配置默认 max_delegation_depth == 3
- agent 工具达上限时拒绝委派
- 委派时 depth 逐层 +1 传播到子代理 inputs，且子代理工具集按深度门控
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from lumi.agents.tools.loader import AgentConfig
from lumi.agents.tools.providers.agent import _child_tools, agent
from lumi.utils.read_config import get_config


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _names(tools: list) -> set[str]:
    return {t.name for t in tools}


# --- 纯函数门控 ---


def test_child_tools_keeps_agent_below_limit() -> None:
    tools = [_FakeTool("agent"), _FakeTool("bash"), _FakeTool("read")]
    assert "agent" in _names(_child_tools(tools, child_depth=1, max_depth=3))
    assert "agent" in _names(_child_tools(tools, child_depth=2, max_depth=3))


def test_child_tools_strips_agent_at_limit() -> None:
    tools = [_FakeTool("agent"), _FakeTool("bash")]
    # child_depth 到顶：子代理不再具备 agent 工具，但其它工具保留
    stripped = _child_tools(tools, child_depth=3, max_depth=3)
    assert "agent" not in _names(stripped)
    assert "bash" in _names(stripped)


def test_child_tools_max_depth_one_strips_immediately() -> None:
    tools = [_FakeTool("agent"), _FakeTool("bash")]
    # max_depth=1：主 agent 委派出的第 1 层子代理即不能再委派
    assert "agent" not in _names(_child_tools(tools, child_depth=1, max_depth=1))


# --- 配置默认值与校验 ---


def test_negative_max_delegation_depth_rejected() -> None:
    """负的委派层数无意义，应被 ge=0 约束拒绝。"""
    from pydantic import ValidationError

    from lumi.utils.config.models import AgentsConfig

    with pytest.raises(ValidationError):
        AgentsConfig(max_delegation_depth=-1)


def test_zero_max_delegation_depth_allowed() -> None:
    """0 表示禁止委派，是合法配置。"""
    from lumi.utils.config.models import AgentsConfig

    assert AgentsConfig(max_delegation_depth=0).max_delegation_depth == 0


def test_default_max_delegation_depth_is_three() -> None:
    assert get_config().config.agents.max_delegation_depth == 3


# --- agent 工具集成行为 ---


def _make_runtime(depth: int) -> SimpleNamespace:
    return SimpleNamespace(
        state={"depth": depth, "tool_mode": "default"},
        context=SimpleNamespace(permission_engine=None),
    )


async def test_refuses_when_at_depth_limit() -> None:
    """current_depth >= max_depth 时直接拒绝，不创建子代理。"""
    runtime = _make_runtime(depth=3)  # 默认 max=3 → 3>=3
    result = await agent.coroutine(name="worker", prompt="干活", runtime=runtime)
    assert "最大委派层数" in result


def _patch_agent_internals(captured: dict):
    """patch agent 工具的重依赖：load_agents / registry / create_agent / run_with_shell。"""

    async def fake_get_tools(names=None):
        return [_FakeTool("agent"), _FakeTool("bash")]

    async def fake_ainvoke(inputs, context=None):
        captured["inputs"] = inputs
        return {"messages": [AIMessage(content="done")]}

    async def fake_create_agent(**kwargs):
        captured["tools"] = kwargs["tools"]
        lumi_agent = SimpleNamespace(graph=SimpleNamespace(ainvoke=fake_ainvoke))
        return lumi_agent, SimpleNamespace()

    async def fake_run_with_shell(key, coro):
        return await coro

    cfg = AgentConfig(name="worker", description="d", system_prompt="p")
    return (
        patch(
            "lumi.agents.tools.providers.agent.load_agents",
            return_value=[cfg],
        ),
        patch(
            "lumi.agents.tools.providers.agent.get_tool_registry",
            return_value=SimpleNamespace(get_tools=fake_get_tools),
        ),
        patch("lumi.agents.core.graph.create_agent", side_effect=fake_create_agent),
        patch(
            "lumi.agents.tools.providers.agent.run_with_shell",
            side_effect=fake_run_with_shell,
        ),
    )


async def test_propagates_incremented_depth_and_keeps_agent_tool() -> None:
    """depth=1 委派：子代理 inputs.depth==2，且 child_depth=2<3 保留 agent 工具。"""
    captured: dict = {}
    p1, p2, p3, p4 = _patch_agent_internals(captured)
    with p1, p2, p3, p4:
        await agent.coroutine(
            name="worker", prompt="干活", runtime=_make_runtime(depth=1)
        )

    assert captured["inputs"]["depth"] == 2
    assert "agent" in _names(captured["tools"])


async def test_strips_agent_tool_for_last_allowed_layer() -> None:
    """depth=2 委派：child_depth=3>=3，子代理 inputs.depth==3 但不再带 agent 工具。"""
    captured: dict = {}
    p1, p2, p3, p4 = _patch_agent_internals(captured)
    with p1, p2, p3, p4:
        await agent.coroutine(
            name="worker", prompt="干活", runtime=_make_runtime(depth=2)
        )

    assert captured["inputs"]["depth"] == 3
    assert "agent" not in _names(captured["tools"])

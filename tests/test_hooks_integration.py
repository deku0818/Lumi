"""Hook 在 graph 节点中的插桩集成测试：Stop / PreToolUse / PostToolUse。

节点级测试（不 mock LLM）：直接构造 state/runtime/config 调用节点函数，
验证 dispatch_hooks 在正确位置被触发且返回值被正确处理。
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

import lumi.agents.core.nodes as nodes_mod
from lumi.agents.core.hooks import AdditionalContext, Block, replace_hooks
from lumi.agents.core.nodes import on_agent_stop, tool_executor
from lumi.agents.core.state import LumiAgentContext


class _FakeToolNode:
    """假 ToolNode：跳过真实工具执行（需 graph runtime），返回固定结果。

    工具执行本身由 test_node_helpers_execution 覆盖；本文件只验证 hook 注入。
    """

    def __init__(self, tools, handle_tool_errors=None):
        self._tools = tools

    async def ainvoke(self, state):
        return {
            "messages": [ToolMessage(content="echo:hi", tool_call_id="1", name="echo")]
        }


def _runtime(tools):
    return SimpleNamespace(context=LumiAgentContext(tools=tools))


def _ai_with_tool_call(name="echo", args=None, tc_id="1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"x": "hi"}, "id": tc_id}],
    )


def _hook(result):
    async def hook(ctx):
        return result

    return hook


# === Stop（on_agent_stop 节点）===


async def test_on_agent_stop_defaults_to_end():
    state = {"messages": [AIMessage(content="done")]}
    cmd = await on_agent_stop(state, {})
    assert cmd.goto == END


async def test_on_agent_stop_hook_pulls_back_to_callmodel():
    state = {"messages": [AIMessage(content="想结束")]}
    with replace_hooks("Stop", [_hook(AdditionalContext("还没填结构化输出"))]):
        cmd = await on_agent_stop(state, {})
    assert cmd.goto == "CallModel"
    text = cmd.update["messages"][0].content[0]["text"]
    assert "还没填结构化输出" in text


async def test_on_agent_stop_hook_block_ends():
    state = {"messages": [AIMessage(content="x")]}
    with replace_hooks("Stop", [_hook(Block("策略终止"))]):
        cmd = await on_agent_stop(state, {})
    assert cmd.goto == END
    assert cmd.update["messages"][0].content == "策略终止"


# === PreToolUse（tool_executor 执行前）===


async def test_pre_tool_use_block_pairs_tool_message_and_ends():
    last = _ai_with_tool_call(name="bash", args={"command": "rm -rf /"}, tc_id="42")
    state = {"messages": [last]}
    with replace_hooks("PreToolUse", [_hook(Block("禁止危险命令"))]):
        cmd = await tool_executor(state, _runtime([]), {})
    assert cmd.goto == END
    msgs = cmd.update["messages"]
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    # 补齐了与 tool_call 配对的 error ToolMessage
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "42"
    assert tool_msgs[0].status == "error"
    assert "禁止危险命令" in tool_msgs[0].content


async def test_pre_tool_use_reminder_injected_after_tool_result(monkeypatch):
    monkeypatch.setattr(nodes_mod, "ToolNode", _FakeToolNode)
    last = _ai_with_tool_call()
    state = {"messages": [last]}
    with replace_hooks("PreToolUse", [_hook(AdditionalContext("注意副作用"))]):
        result = await tool_executor(state, _runtime([]), {})
    msgs = result["messages"]
    # 工具仍执行：有 echo 的 ToolMessage
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert any("echo:hi" in m.content for m in tool_msgs)
    # reminder 注入在 ToolMessage 之后
    reminders = [m for m in msgs if isinstance(m, HumanMessage)]
    assert reminders and "注意副作用" in reminders[-1].content[0]["text"]
    assert msgs.index(tool_msgs[-1]) < msgs.index(reminders[-1])


# === PostToolUse（tool_executor 执行后）===


async def test_post_tool_use_reminder_appended(monkeypatch):
    monkeypatch.setattr(nodes_mod, "ToolNode", _FakeToolNode)
    last = _ai_with_tool_call()
    state = {"messages": [last]}
    with replace_hooks("PostToolUse", [_hook(AdditionalContext("记得 git status"))]):
        result = await tool_executor(state, _runtime([]), {})
    msgs = result["messages"]
    reminders = [m for m in msgs if isinstance(m, HumanMessage)]
    assert reminders and "记得 git status" in reminders[-1].content[0]["text"]


async def test_no_hooks_tool_executor_unchanged(monkeypatch):
    monkeypatch.setattr(nodes_mod, "ToolNode", _FakeToolNode)
    last = _ai_with_tool_call()
    state = {"messages": [last]}
    result = await tool_executor(state, _runtime([]), {})
    msgs = result["messages"]
    # 无 hook 时只有工具结果，无额外注入
    assert all(not isinstance(m, HumanMessage) for m in msgs)
    assert any(isinstance(m, ToolMessage) and "echo:hi" in m.content for m in msgs)

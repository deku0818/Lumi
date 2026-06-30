"""每轮上下文块（turn_context）单元测试。

覆盖：env 恒注入、agent（按名排序 + 工具门控）、skill、记忆/LUMI.md 的组装；
**字节稳定性**（同输入 build 两次逐字节一致——缓存正确性的前提）；call_model 经
turn_context 传给 tool_call_chain；chain 把它作 HumanMessage 插在静态 system 之后。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lumi.agents.core import nodes
from lumi.agents.core.preprocessing import turn_context
from lumi.agents.core.preprocessing.turn_context import (
    build_turn_context,
    build_turn_context_text,
)


def _cfg(name: str, desc: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc)


@contextmanager
def _patched(agents=(), skills=(), project_dir=None):
    """mock detector + 授权目录；project_dir=None 时记忆/LUMI.md 注入为空。"""
    proj = project_dir or Path("/nonexistent-proj")
    with (
        patch.object(turn_context.AgentChangeDetector, "get_instance") as ai,
        patch.object(turn_context.SkillChangeDetector, "get_instance") as si,
        patch.object(turn_context, "get_authorized_directory", return_value=proj),
    ):
        ai.return_value.check.return_value = (list(agents), False)
        si.return_value.check.return_value = (list(skills), False)
        yield


def test_env_always_present():
    with _patched():
        text = build_turn_context_text(memory_enabled=False, has_agent_tool=False)
    assert "用户当前系统环境信息" in text  # env block 恒在


def test_agents_included_and_sorted_when_tool_available():
    agents = [_cfg("zeta", "z"), _cfg("alpha", "a")]
    with _patched(agents=agents):
        text = build_turn_context_text(memory_enabled=False, has_agent_tool=True)
    assert "zeta" in text and "alpha" in text
    assert text.index("alpha") < text.index("zeta")  # 按名排序，确定性


def test_agents_skipped_without_agent_tool():
    agents = [_cfg("alpha", "a")]
    with _patched(agents=agents):
        text = build_turn_context_text(memory_enabled=False, has_agent_tool=False)
    assert "alpha" not in text  # 无 agent 工具不注入代理列表


def test_skills_included():
    with _patched(skills=[_cfg("myskill", "do x")]):
        text = build_turn_context_text(memory_enabled=False, has_agent_tool=False)
    assert "myskill" in text


def test_memory_index_gated_by_memory_enabled(tmp_path, monkeypatch):
    from lumi.agents.memory import ensure_memory_dir, memory_entrypoint
    from lumi.agents.memory import paths as memory_paths

    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    proj.mkdir()
    ensure_memory_dir(proj)
    memory_entrypoint(proj).write_text(
        "- [角色](u.md) — 后端工程师\n", encoding="utf-8"
    )

    with _patched(project_dir=proj):
        on = build_turn_context_text(memory_enabled=True, has_agent_tool=False)
        off = build_turn_context_text(memory_enabled=False, has_agent_tool=False)
    assert "后端工程师" in on
    assert "后端工程师" not in off  # 子 agent（memory_enabled=False）不带记忆索引


def test_byte_stability_same_inputs(tmp_path, monkeypatch):
    """同输入 build 两次必须逐字节相同——否则每轮破缓存。"""
    from lumi.agents.memory import ensure_memory_dir, memory_entrypoint
    from lumi.agents.memory import paths as memory_paths

    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    proj.mkdir()
    ensure_memory_dir(proj)
    # 带 [type · 绝对日期] 的新索引行格式：绝对日期是静态值，build 两次仍逐字节相同
    memory_entrypoint(proj).write_text(
        "- [a](a.md) [feedback · 2026-06-20] — x\n", encoding="utf-8"
    )
    (proj / "LUMI.md").write_text("项目约定", encoding="utf-8")

    agents = [_cfg("b", "B"), _cfg("a", "A")]
    skills = [_cfg("s2", "二"), _cfg("s1", "一")]
    with _patched(agents=agents, skills=skills, project_dir=proj):
        first = build_turn_context_text(memory_enabled=True, has_agent_tool=True)
        second = build_turn_context_text(memory_enabled=True, has_agent_tool=True)
    assert first == second  # 字节稳定 → 稳定缓存前缀


def test_build_turn_context_from_runtime():
    runtime = SimpleNamespace(context=SimpleNamespace(tools=[], memory_enabled=False))
    with _patched():
        text = build_turn_context(runtime)
    assert "用户当前系统环境信息" in text  # env 恒在


async def test_call_model_passes_turn_context_to_chain(monkeypatch):
    """call_model 把上下文块作为 turn_context 传给 tool_call_chain（不再 prepend 消息）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    captured: dict = {}

    class FakeChain:
        async def ainvoke(self, payload):
            captured["messages"] = list(payload["messages"])
            return AIMessage(content="ok", tool_calls=[])

    def fake_tool_call_chain(*args, **kwargs):
        captured["turn_context"] = kwargs.get("turn_context")
        return FakeChain()

    monkeypatch.setattr(nodes, "tool_call_chain", fake_tool_call_chain)

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            system_prompt="s", model_name="gpt-4o", tools=[], memory_enabled=True
        )
    )
    state = {
        "messages": [HumanMessage(content="hi")],
        "iterations": 1,
        "output_schema": None,
    }
    with _patched():
        await nodes.call_model(state, runtime)

    assert (
        "用户当前系统环境信息" in captured["turn_context"]
    )  # ctx 经 turn_context 传入
    assert captured["messages"][0].content == "hi"  # ctx 不再混进 messages


def test_turn_context_inserter_places_ctx_after_system():
    """turn_context 作为 HumanMessage 插在所有 system 之后、历史之前。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from lumi.models.chain import _turn_context_inserter

    out = _turn_context_inserter("CTX").invoke(
        [SystemMessage("S"), HumanMessage("hi"), AIMessage("a")]
    )
    assert [type(m).__name__ for m in out] == [
        "SystemMessage",
        "HumanMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert out[1].content == "CTX"  # ctx 紧跟 system
    assert out[2].content == "hi"  # 历史保留
    # 无 system（default 风格空 system_prompt）→ ctx 在最前
    assert (
        _turn_context_inserter("CTX").invoke([HumanMessage("hi")])[0].content == "CTX"
    )


def test_tool_call_chain_ctx_is_human_after_pure_system():
    """ctx 作 HumanMessage 插在纯静态 system 之后（trim 之后插入 → 免截断；非第二条 system →
    避开兼容 provider 不支持连续 system 的问题；静态 system 纯净 → 独立缓存）。CC 同构。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage, SystemMessage

    from lumi.models import chain

    fake = ChatAnthropic(model="claude-sonnet-4-5", api_key="x")
    with patch.object(chain, "create_llm", return_value=fake):
        c = chain.tool_call_chain([], system_prompt="STATIC", turn_context="CTX")
    inner = c.bound  # prompt | trim | inserter | llm
    upto_inserter = inner.steps[0] | inner.steps[1] | inner.steps[2]
    out = upto_inserter.invoke({"messages": [HumanMessage("hi")]})
    assert isinstance(out[0], SystemMessage)
    assert out[0].content[0]["text"] == "STATIC"  # system 纯静态（无 ctx）
    assert "cache_control" in out[0].content[0]  # 静态 system 独立缓存断点
    assert (
        isinstance(out[1], HumanMessage) and out[1].content == "CTX"
    )  # ctx 紧跟 system
    assert out[2].content == "hi"  # 历史在后


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

"""auto_dream_stop_hook 门控阶梯测试：各廉价门放行（return None）。

「全过 → 启动后台 dream」涉及真实 checkpointer + 多会话 + LLM，属端到端验证，不在此覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace

from lumi.agents.core.hooks.schema import HookContext
from lumi.agents.memory.dream import auto_dream_stop_hook


def _runtime(memory_enabled=True, engine=None):
    return SimpleNamespace(
        context=SimpleNamespace(memory_enabled=memory_enabled, permission_engine=engine)
    )


def _ctx(state, runtime):
    return HookContext(state=state, config={}, event="Stop", runtime=runtime)


async def test_depth_gate_blocks_subagent():
    """depth>0（dream agent 自身 / 子 agent）→ 首门直接放行，防自递归。"""
    assert await auto_dream_stop_hook(_ctx({"depth": 1}, _runtime())) is None


async def test_no_runtime():
    assert await auto_dream_stop_hook(_ctx({"depth": 0}, None)) is None


async def test_memory_disabled():
    """memory_enabled=False（子 agent / cron / 后台）→ 放行。"""
    rt = _runtime(memory_enabled=False)
    assert await auto_dream_stop_hook(_ctx({"depth": 0}, rt)) is None


async def test_output_schema_skipped():
    """结构化输出轮当非交互 → 放行。"""
    ctx = _ctx({"depth": 0, "output_schema": {"x": 1}}, _runtime())
    assert await auto_dream_stop_hook(ctx) is None


async def test_config_disabled_by_default():
    """memory_enabled=True 但 auto_dream.enabled 默认 False → 放行（opt-in）。"""
    assert await auto_dream_stop_hook(_ctx({"depth": 0}, _runtime())) is None

"""auto_dream_stop_hook 门控阶梯测试：各廉价门放行（return None）。

「全过 → 启动后台 dream」涉及真实 checkpointer + 多会话 + LLM，属端到端验证，不在此覆盖。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from lumi.agents.core.hooks.schema import HookContext
from lumi.agents.core.meta_message import meta_human_message
from lumi.agents.memory import dream as dream_mod
from lumi.agents.memory import dream_lock
from lumi.agents.memory.dream import _human_delta, auto_dream_stop_hook, start_dream
from lumi.sessions.message_visibility import count_human_messages


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


# --- /dream 主动触发（start_dream）---


def _engine_ctx(project_dir):
    return SimpleNamespace(permission_engine=SimpleNamespace(project_dir=project_dir))


async def test_start_dream_no_workspace():
    """workspace 为空 → 不启动，提示未绑定项目。"""
    r = await start_dream(_engine_ctx(Path("/p")), [], "", "t")
    assert "未绑定项目" in r


async def test_start_dream_in_flight(monkeypatch, tmp_path):
    """已有 dream 在跑 → 不重复启动。"""
    from lumi.agents.memory import paths as memory_paths

    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    proj = tmp_path / "proj"
    dream_lock.mark_in_flight(proj)
    try:
        assert "进行中" in await start_dream(_engine_ctx(proj), [], "/proj", "t")
    finally:
        dream_lock.clear_in_flight(proj)


async def test_start_dream_spawns_force(monkeypatch, tmp_path):
    """正常路径 → force 启动后台 dream，返回启动提示。"""
    from lumi.agents.memory import paths as memory_paths

    monkeypatch.setattr(memory_paths, "MEMORY_ROOT", tmp_path / "mem")
    spawned: list = []
    monkeypatch.setattr(dream_mod, "_spawn_dream", lambda *a, **k: spawned.append(k))
    r = await start_dream(_engine_ctx(tmp_path / "proj2"), [], "/proj", "t")
    assert spawned and spawned[0].get("force") is True
    assert "已在后台" in r


# --- human 门 delta（_human_delta）---


def test_delta_new_session():
    """新会话无游标 → 全部真实 human 算新增。"""
    assert _human_delta({"t": 3}, {}) == 3


def test_delta_old_messages_not_polluting():
    """老会话游标 50、当前 51 → 只算新增 1（旧消息不撑门，这是换 human 门的核心）。"""
    assert _human_delta({"t": 51}, {"t": 50}) == 1


def test_delta_max0_on_compact():
    """compact 删消息致当前 < 游标 → max(0) 防负。"""
    assert _human_delta({"t": 5}, {"t": 20}) == 0


def test_delta_sum_multi():
    """多会话求和：新会话 3 + 老会话新增 1 = 4（老会话的 50 条旧消息不计）。"""
    assert _human_delta({"a": 3, "b": 51}, {"b": 50}) == 4


# --- count_human_messages ---


def test_count_human_excludes_meta():
    """只数真实 HumanMessage，排除 meta/reminder 注入与 ai 消息。"""
    msgs = [
        HumanMessage("hi"),
        AIMessage("ok"),
        meta_human_message("reminder"),
        HumanMessage("bye"),
    ]
    assert count_human_messages(msgs) == 2


def test_count_human_dict_format():
    """兼容 dict 格式消息（checkpoint 恢复路径可能是 dict，与 _extract_first_human_message 一致）。"""
    msgs = [
        {"type": "human", "content": "hi"},
        {"type": "ai", "content": "ok"},
        HumanMessage("bye"),
    ]
    assert count_human_messages(msgs) == 2

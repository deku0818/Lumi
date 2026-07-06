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
from lumi.agents.memory.dream import auto_dream_stop_hook, start_dream
from lumi.sessions.message_visibility import latest_human_ts
from lumi.utils.constants import LUMI_META_KEY


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


# --- latest_human_ts（IM 长会话判活的度量）---


def _human_with_ts(text: str, ts_ms: int) -> HumanMessage:
    return HumanMessage(text, additional_kwargs={LUMI_META_KEY: {"ts": ts_ms}})


def test_latest_ts_takes_newest_real_human():
    """取真实 human 的最新落库 ts（毫秒 → 秒）；ai / meta 注入不计。"""
    msgs = [
        _human_with_ts("hi", 1_000_000),
        AIMessage("ok", additional_kwargs={LUMI_META_KEY: {"ts": 9_999_999}}),
        meta_human_message("reminder"),
        _human_with_ts("bye", 2_000_000),
    ]
    assert latest_human_ts(msgs) == 2000.0


def test_latest_ts_zero_when_no_ts():
    """无 ts 的 human（压缩载体 / 旧消息）不计；一条带 ts 的都没有 → 0.0。"""
    assert latest_human_ts([HumanMessage("旧"), AIMessage("答")]) == 0.0
    assert latest_human_ts([]) == 0.0


def test_latest_ts_dict_format():
    """兼容 dict 格式消息（checkpoint 恢复路径可能是 dict）。"""
    msgs = [
        {
            "type": "human",
            "content": "hi",
            "additional_kwargs": {LUMI_META_KEY: {"ts": 5_000_000}},
        },
        HumanMessage("无 ts"),
    ]
    assert latest_human_ts(msgs) == 5000.0

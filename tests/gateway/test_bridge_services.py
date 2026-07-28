"""AgentBridge service 子模块特征测试（bridge 拆包安全网）。

ProviderService：list / set / save / delete 经 bridge 委派往返 provider_store。
CheckpointService：构造 + 未初始化 shadow 时的基本调用 smoke。
均不初始化真实 Agent graph（参考 tests/test_bridge_workspace.py 的构造方式）。
"""

from __future__ import annotations

import pytest

from lumi.gateway.bridge import AgentBridge
from lumi.gateway.bridge.checkpoint import CheckpointService
from lumi.gateway.bridge.providers import ProviderService
from lumi.models import provider_store
from lumi.utils.config import user_store
from lumi.utils.read_config import get_config


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 lumi.json 指向 tmp 目录，隔离真实 ~/.lumi。"""
    target = tmp_path / "lumi.json"
    monkeypatch.setattr(user_store, "CONFIG_FILE", target)
    return target


def _profile(name="A", base="u", key="k", models=("m1", "m2")):
    return {"name": name, "base_url": base, "api_key": key, "models": list(models)}


# ── ProviderService ──


def test_bridge_wires_services():
    bridge = AgentBridge()
    assert isinstance(bridge._providers, ProviderService)
    assert isinstance(bridge._checkpoint, CheckpointService)
    assert bridge._providers._bridge is bridge
    assert bridge._checkpoint._bridge is bridge


def test_list_providers_empty(store_path):
    bridge = AgentBridge()
    result = bridge.list_providers()
    cfg = get_config().config
    assert result == {
        "profiles": [],
        "active": {"provider": "", "model": ""},
        "classifier": {},
        "titler": {},
        # 兜底值随结果下发，前端不再硬编码「未探测到时用多少」
        "fallback": {
            "context": cfg.token.context_length,
            "max_tokens": cfg.agents.max_tokens,
        },
    }


def test_save_provider_persists_and_lists(store_path):
    bridge = AgentBridge()
    result = bridge.save_provider(_profile())
    assert len(result["profiles"]) == 1
    p = result["profiles"][0]
    assert p["name"] == "A"
    assert p["models"] == ["m1", "m2"]
    # 首次 upsert 自动成为 active 的首个模型
    assert result["active"]["model"] == "m1"
    # 持久化往返：重新 load 一致
    profiles, _ = provider_store.load()
    assert len(profiles) == 1 and profiles[0].name == "A"


def test_save_provider_keeps_limit_overrides(store_path):
    """按模型的上下文/输出覆盖经表单落盘，并原样回到 list 结果（可直接回填表单）。"""
    bridge = AgentBridge()
    result = bridge.save_provider(
        {**_profile(), "context": {"m1": 262144}, "max_tokens": {"m1": 4096}}
    )
    p = result["profiles"][0]
    assert p["context"] == {"m1": 262144, "m2": 0}
    assert p["max_tokens"] == {"m1": 4096, "m2": 0}
    # 覆盖值即生效值（m1 目录里查不到，无覆盖的 m2 则为 0 = 未知）
    assert p["context_window"]["m1"] == 262144
    assert p["context_window"]["m2"] == 0
    profiles, _ = provider_store.load()
    assert profiles[0].context == {"m1": 262144}


def test_save_provider_clearing_limit_restores_auto(store_path):
    """表单清空该格 = 删除覆盖（不是存 0）：编辑表单是限制覆盖的唯一入口。"""
    bridge = AgentBridge()
    bridge.save_provider({**_profile(), "context": {"m1": 262144}})
    pid = provider_store.load()[1]["provider"]
    bridge.save_provider({**_profile(), "id": pid, "context": {}})
    profiles, _ = provider_store.load()
    assert profiles[0].context == {}


@pytest.mark.parametrize(
    "raw", [{"m1": 0}, {"m1": -1}, {"m1": "abc"}, {"m1": None}, {"nosuch": 100}, "junk"]
)
def test_save_provider_rejects_bad_limits(store_path, raw):
    """0 / 负数 / 非数字 / 不存在的模型一律不落盘——留下就会被当成真上限用。"""
    bridge = AgentBridge()
    bridge.save_provider({**_profile(), "context": raw})
    profiles, _ = provider_store.load()
    assert profiles[0].context == {}


def test_set_provider_switches_active(store_path):
    bridge = AgentBridge()
    bridge.save_provider(_profile())
    pid = provider_store.load()[1]["provider"]
    result = bridge.set_provider(pid, "m2")
    assert result["active"] == {"provider": pid, "model": "m2"}


def test_set_provider_unknown_raises(store_path):
    bridge = AgentBridge()
    with pytest.raises(ValueError):
        bridge.set_provider("nope", "m1")


def test_set_effort_auto_returns_and_clears(store_path):
    bridge = AgentBridge()
    bridge.save_provider(_profile())
    pid = provider_store.load()[1]["provider"]
    # auto 永远合法；存储语义为"未设置"（不落盘条目，恢复默认不传参）
    assert bridge.set_effort(pid, "m1", "auto") == {"effort": "auto"}
    profiles, _ = provider_store.load()
    assert "m1" not in profiles[0].effort


def test_set_effort_unknown_raises(store_path):
    bridge = AgentBridge()
    bridge.save_provider(_profile())
    pid = provider_store.load()[1]["provider"]
    with pytest.raises(ValueError):
        bridge.set_effort(pid, "missing-model", "auto")


def test_delete_provider_removes(store_path):
    bridge = AgentBridge()
    bridge.save_provider(_profile())
    pid = provider_store.load()[1]["provider"]
    result = bridge.delete_provider(pid)
    cfg = get_config().config
    assert result == {
        "profiles": [],
        "active": {"provider": "", "model": ""},
        "classifier": {},
        "titler": {},
        "fallback": {
            "context": cfg.token.context_length,
            "max_tokens": cfg.agents.max_tokens,
        },
    }
    assert provider_store.load()[0] == []


# ── CheckpointService ──


async def test_list_checkpoints_no_shadow_returns_empty():
    bridge = AgentBridge()
    assert bridge._shadow is None
    assert await bridge.list_checkpoints() == []


async def test_create_checkpoint_before_turn_no_shadow_noop():
    bridge = AgentBridge()
    # 无 shadow 时静默返回，不抛错
    await bridge._create_checkpoint_before_turn("hello")


async def test_rewind_no_shadow_returns_error():
    bridge = AgentBridge()
    ok, msg = await bridge.rewind_to_checkpoint(object())  # shadow 未初始化即早返回
    assert ok is False
    assert msg == "Checkpoint 未初始化"

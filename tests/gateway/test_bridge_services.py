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


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 providers.json 指向 tmp 目录，隔离真实 ~/.lumi。"""
    target = tmp_path / "providers.json"
    monkeypatch.setattr(provider_store, "_path", lambda: target)
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
    assert result == {"profiles": [], "active": {"provider": "", "model": ""}}


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
    assert result == {"profiles": [], "active": {"provider": "", "model": ""}}
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

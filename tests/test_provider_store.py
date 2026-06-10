"""provider_store 持久化单元测试（隔离到 tmp 目录，不碰真实 ~/.lumi）。"""

from __future__ import annotations

import json
import stat

import pytest

from lumi.agents.runtime import provider_store


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 providers.json 指向 tmp 目录。"""
    target = tmp_path / "providers.json"
    monkeypatch.setattr(provider_store, "_path", lambda: target)
    return target


def _p(name="A", base="u", key="k", models=("m1",)):
    return {"name": name, "base_url": base, "api_key": key, "models": list(models)}


def test_load_missing_returns_empty(store_path):
    assert provider_store.load() == ([], {"provider": "", "model": ""})
    assert provider_store.get_active() is None


def test_upsert_first_model_becomes_active(store_path):
    saved = provider_store.upsert(_p("我的代理", models=("claude-opus-4-6", "gpt-4o")))
    profiles, active = provider_store.load()
    assert active == {"provider": saved.id, "model": "claude-opus-4-6"}
    assert len(profiles) == 1 and profiles[0].models == ("claude-opus-4-6", "gpt-4o")
    prof, model = provider_store.get_active()
    assert prof.name == "我的代理" and model == "claude-opus-4-6"


def test_upsert_dedupes_and_strips_models(store_path):
    s = provider_store.upsert(_p(models=("m1", " m1 ", "m2", "")))
    assert s.models == ("m1", "m2")


def test_set_active_to_another_model_same_provider(store_path):
    s = provider_store.upsert(_p(models=("m1", "m2")))
    assert provider_store.set_active(s.id, "m2") == {"provider": s.id, "model": "m2"}
    assert provider_store.load()[1] == {"provider": s.id, "model": "m2"}


def test_set_active_rejects_unknown_model_or_provider(store_path):
    s = provider_store.upsert(_p(models=("m1",)))
    assert provider_store.set_active(s.id, "nope") is None
    assert provider_store.set_active("badid", "m1") is None


def test_editing_active_provider_removing_model_renormalizes(store_path):
    s = provider_store.upsert(_p(models=("m1", "m2")))
    provider_store.set_active(s.id, "m2")
    # 编辑该 provider，去掉 m2 → active 失效，应回退到剩余 m1
    provider_store.upsert({"id": s.id, **_p(models=("m1",))})
    assert provider_store.load()[1] == {"provider": s.id, "model": "m1"}


def test_delete_active_provider_reassigns(store_path):
    a = provider_store.upsert(_p("A", models=("m1",)))
    b = provider_store.upsert(_p("B", models=("n1",)))
    provider_store.set_active(b.id, "n1")
    provider_store.delete(b.id)
    profiles, active = provider_store.load()
    assert [p.id for p in profiles] == [a.id]
    assert active == {"provider": a.id, "model": "m1"}


def test_legacy_single_model_format_migrates(store_path):
    # 旧格式：profile 用单个 "model" 字段，active 为字符串 id
    store_path.write_text(
        json.dumps(
            {
                "active": "old1",
                "profiles": [
                    {
                        "id": "old1",
                        "name": "旧",
                        "base_url": "u",
                        "api_key": "k",
                        "model": "claude-x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    profiles, active = provider_store.load()
    assert profiles[0].models == ("claude-x",)
    assert active == {"provider": "old1", "model": "claude-x"}


def test_file_is_chmod_600_and_plaintext(store_path):
    provider_store.upsert(_p(key="sk-secret", models=("m1",)))
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["profiles"][0]["api_key"] == "sk-secret"
    assert data["profiles"][0]["models"] == ["m1"]

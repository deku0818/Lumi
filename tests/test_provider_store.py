"""provider_store 持久化单元测试（隔离到 tmp 目录，不碰真实 ~/.lumi）。"""

from __future__ import annotations

import json
import stat

import pytest

from lumi.models import provider_store


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """把 providers.json 指向 tmp 目录。"""
    target = tmp_path / "providers.json"
    monkeypatch.setattr(provider_store, "_path", lambda: target)
    return target


def _p(name="A", base="u", key="k", models=("m1",)):
    return {"name": name, "base_url": base, "api_key": key, "models": list(models)}


def test_load_missing_returns_empty(store_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_NAME", "env-model")
    assert provider_store.load() == ([], {"provider": "", "model": ""})
    # 无任何 profile 时 resolve 回退 env 默认模型、无连接覆盖
    assert provider_store.resolve() == provider_store.ResolvedModel("env-model", "", "")


def test_upsert_first_model_becomes_active(store_path):
    saved = provider_store.upsert(_p("我的代理", models=("claude-opus-4-6", "gpt-4o")))
    profiles, active = provider_store.load()
    assert active == {"provider": saved.id, "model": "claude-opus-4-6"}
    assert len(profiles) == 1 and profiles[0].models == ("claude-opus-4-6", "gpt-4o")
    assert provider_store.resolve() == provider_store.ResolvedModel(
        "claude-opus-4-6", "u", "k"
    )


def test_resolve_named_model_prefers_active_profile(store_path):
    a = provider_store.upsert(_p("A", base="ua", key="ka", models=("m1",)))
    b = provider_store.upsert(_p("B", base="ub", key="kb", models=("m1", "m2")))
    provider_store.set_active(a.id, "m1")
    # 同名模型存在于多个 profile：active 优先
    assert provider_store.resolve("m1") == provider_store.ResolvedModel(
        "m1", "ua", "ka"
    )
    # 不在 active profile 的模型：兜底反查其他 profile
    assert provider_store.resolve("m2") == provider_store.ResolvedModel(
        "m2", "ub", "kb"
    )
    assert b.id != a.id


def test_resolve_unknown_model_has_no_connection(store_path):
    provider_store.upsert(_p(models=("m1",)))
    assert provider_store.resolve("other") == provider_store.ResolvedModel(
        "other", "", ""
    )


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


def test_effort_per_model_set_and_resolve(store_path, monkeypatch):
    # 隔离 models.dev 能力：m1 支持 low/high，m2 仅 auto
    monkeypatch.setattr(
        provider_store,
        "allowed_levels",
        lambda m: ("auto", "low", "high") if m == "m1" else ("auto",),
    )
    s = provider_store.upsert(_p(models=("m1", "m2")))
    assert provider_store.set_effort(s.id, "m1", "high") == "high"
    assert provider_store.resolve("m1").effort == "high"
    assert provider_store.resolve("m2").effort == "auto"
    # 非法档位 / 不存在的 model / provider → None
    assert provider_store.set_effort(s.id, "m1", "nope") is None
    assert provider_store.set_effort(s.id, "m2", "high") is None
    assert provider_store.set_effort("badid", "m1", "low") is None
    # auto 即删除记录
    assert provider_store.set_effort(s.id, "m1", "auto") == "auto"
    assert provider_store.load()[0][0].effort == {}


def test_effort_survives_profile_edit(store_path, monkeypatch):
    monkeypatch.setattr(provider_store, "allowed_levels", lambda m: ("auto", "max"))
    s = provider_store.upsert(_p(models=("m1", "m2")))
    provider_store.set_effort(s.id, "m1", "max")
    # 编辑 profile（不带 effort 字段）→ 档位记忆保留；删掉的模型记录被清理
    provider_store.upsert({"id": s.id, **_p(models=("m1",))})
    prof = provider_store.load()[0][0]
    assert prof.effort == {"m1": "max"}


def test_classifier_unset_falls_back_to_session_model(store_path):
    """未配 classifier：get 为空，resolve_classifier 回退会话 active 模型。"""
    s = provider_store.upsert(_p(base="ua", key="ka", models=("m1",)))
    assert provider_store.get_classifier() == {}
    # 回退 = resolve()（会话模型 + 其连接）
    assert provider_store.resolve_classifier() == provider_store.ResolvedModel(
        "m1", "ua", "ka"
    )
    assert s.models == ("m1",)


def test_classifier_set_resolves_exact_connection(store_path):
    """classifier 指向另一 profile：按 provider id 精确取该 profile 的连接。"""
    provider_store.upsert(_p("A", base="ua", key="ka", models=("m1",)))
    b = provider_store.upsert(_p("B", base="ub", key="kb", models=("haiku",)))
    assert provider_store.set_classifier(b.id, "haiku") == {
        "provider": b.id,
        "model": "haiku",
    }
    assert provider_store.get_classifier() == {"provider": b.id, "model": "haiku"}
    # active 仍是 A 的 m1，但分类器解析到 B 的连接
    assert provider_store.resolve_classifier() == provider_store.ResolvedModel(
        "haiku", "ub", "kb"
    )


def test_classifier_clear_and_invalid_pointer(store_path):
    s = provider_store.upsert(_p(models=("m1",)))
    provider_store.set_classifier(s.id, "m1")
    # 空参数 → 清除
    assert provider_store.set_classifier("", "") == {}
    assert provider_store.get_classifier() == {}
    # 指向不存在的 model → 规范化丢弃（视为未配，回退会话模型）
    assert provider_store.set_classifier(s.id, "nope") == {}
    assert provider_store.get_classifier() == {}


def test_classifier_survives_unrelated_writes(store_path, monkeypatch):
    """set_effort / set_active 等写操作不丢失 classifier 指针。"""
    monkeypatch.setattr(provider_store, "allowed_levels", lambda m: ("auto", "high"))
    s = provider_store.upsert(_p(models=("m1", "m2")))
    provider_store.set_classifier(s.id, "m2")
    provider_store.set_effort(s.id, "m1", "high")
    provider_store.set_active(s.id, "m1")
    assert provider_store.get_classifier() == {"provider": s.id, "model": "m2"}


def test_classifier_auto_cleared_when_target_deleted(store_path):
    a = provider_store.upsert(_p("A", models=("m1",)))
    b = provider_store.upsert(_p("B", models=("haiku",)))
    provider_store.set_classifier(b.id, "haiku")
    # 删除分类器所在 profile → 指针自动失效清空
    provider_store.delete(b.id)
    assert provider_store.get_classifier() == {}
    assert a.id != b.id


def test_file_is_chmod_600_and_plaintext(store_path):
    provider_store.upsert(_p(key="sk-secret", models=("m1",)))
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["profiles"][0]["api_key"] == "sk-secret"
    assert data["profiles"][0]["models"] == ["m1"]

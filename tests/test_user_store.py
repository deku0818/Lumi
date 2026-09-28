"""user_store 单文件多分区共享存储测试（隔离到 tmp，不碰真实 ~/.lumi）。"""

from __future__ import annotations

import stat

import pytest

from lumi.utils.config import user_store


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(user_store, "CONFIG_FILE", tmp_path / "lumi.json")
    return tmp_path / "lumi.json"


def test_read_missing_returns_default():
    assert user_store.read_section("providers", {}) == {}
    assert user_store.read_section("projects", []) == []


def test_write_then_read_roundtrip(tmp_config):
    user_store.write_section("channels", {"feishu": {"enabled": True}})
    assert user_store.read_section("channels", {}) == {"feishu": {"enabled": True}}
    assert stat.S_IMODE(tmp_config.stat().st_mode) == 0o600


def test_section_writes_do_not_clobber_each_other():
    user_store.write_section("projects", [{"path": "/a"}])
    user_store.write_section("providers", {"active": {"provider": "p"}})
    assert user_store.read_section("projects", []) == [{"path": "/a"}]
    assert user_store.read_section("providers", {}) == {"active": {"provider": "p"}}


def test_read_section_wrong_type_returns_default():
    """分区值类型与 default 不符（文件损坏）时回落 default；default 为 None 不约束。"""
    user_store.write_section("providers", ["not", "a", "dict"])
    assert user_store.read_section("providers", {}) == {}  # list ≠ dict → 默认
    assert user_store.read_section("providers", None) == ["not", "a", "dict"]  # 不约束


def test_read_survives_invalid_utf8(tmp_config):
    """lumi.json 含非法 UTF-8 字节：_read_all 回落，不冒泡崩溃。"""
    tmp_config.write_bytes(b'{"providers": "\xff\xfe bad utf8"}')
    assert user_store.read_section("providers", {"fallback": 1}) == {"fallback": 1}


def test_global_config_load_survives_non_dict_settings(tmp_config):
    """settings 分区被损坏成非 dict：GlobalConfigManager.load 回落默认，不抛 TypeError。"""
    from lumi.utils.config.global_manager import GlobalConfigManager

    user_store.write_section("settings", ["not", "a", "dict"])
    assert GlobalConfigManager.load().checkpoint_dir == ""


def test_write_on_corrupt_file_keeps_original_aside(tmp_config):
    """损坏的 lumi.json 不能被一次分区写入抹掉（里面是全部密钥）：原文件移到
    .corrupt-* 留档，新文件从空开始写，后续写入照常可用。"""
    broken = '{"providers": {"active": {"api_key": "sk-keep"}},, }'
    tmp_config.write_text(broken, encoding="utf-8")
    user_store.write_section("projects", [{"path": "/a"}])
    kept = list(tmp_config.parent.glob("lumi.json.corrupt-*"))
    assert [p.read_text(encoding="utf-8") for p in kept] == [broken]
    assert user_store.read_section("projects", []) == [{"path": "/a"}]

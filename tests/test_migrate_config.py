"""scripts/migrate_config.py 一次性迁移脚本测试（隔离到 tmp）。"""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_config.py"
_spec = importlib.util.spec_from_file_location("migrate_config", _SCRIPT)
migrate_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_config)


# ── 用户级：四文件 → 合并 lumi.json ──


def test_merges_legacy_files_and_removes_them(tmp_path):
    (tmp_path / "lumi.json").write_text(  # 旧扁平 settings，含废弃字段应被抹掉
        json.dumps({"max_checkpoints": 5, "theme_mode": "dark", "initialized": True}),
        encoding="utf-8",
    )
    (tmp_path / "projects.json").write_text(
        json.dumps([{"path": "/x"}]), encoding="utf-8"
    )
    (tmp_path / "providers.json").write_text(
        json.dumps({"active": {"provider": "p", "model": "m"}}), encoding="utf-8"
    )
    (tmp_path / "channels.json").write_text(
        json.dumps({"feishu": {"enabled": False}}), encoding="utf-8"
    )

    assert migrate_config.migrate_user_store(tmp_path) is not None

    merged = json.loads((tmp_path / "lumi.json").read_text("utf-8"))
    assert set(merged) == {"settings", "projects", "providers", "channels"}
    assert merged["settings"] == {
        "checkpoint_dir": "",
        "max_checkpoints": 5,
        "stale_thread_days": 30,
    }  # 废弃字段已抹
    assert merged["providers"]["active"]["model"] == "m"
    assert stat.S_IMODE((tmp_path / "lumi.json").stat().st_mode) == 0o600
    for name in ("projects.json", "providers.json", "channels.json"):
        assert not (tmp_path / name).exists()


def test_skips_when_already_merged(tmp_path):
    (tmp_path / "lumi.json").write_text(
        json.dumps({"providers": {"active": {"provider": "keep"}}}), encoding="utf-8"
    )
    (tmp_path / "providers.json").write_text(
        json.dumps({"active": {"provider": "legacy"}}), encoding="utf-8"
    )
    assert migrate_config.migrate_user_store(tmp_path) is None  # 已是新格式
    merged = json.loads((tmp_path / "lumi.json").read_text("utf-8"))
    assert merged["providers"]["active"]["provider"] == "keep"  # 未被 legacy 覆盖
    assert (tmp_path / "providers.json").exists()  # 未动


def test_keeps_corrupt_legacy_file(tmp_path):
    """损坏的旧文件不并入、也不删除（保留供手动修复），不牵连其它分区。"""
    (tmp_path / "projects.json").write_text(
        json.dumps([{"path": "/x"}]), encoding="utf-8"
    )
    (tmp_path / "providers.json").write_text(
        "{ truncated", encoding="utf-8"
    )  # 损坏、含密钥
    migrate_config.migrate_user_store(tmp_path)
    merged = json.loads((tmp_path / "lumi.json").read_text("utf-8"))
    assert "providers" not in merged  # 未并入
    assert (tmp_path / "providers.json").exists()  # 未删，api_key 可救回
    assert not (tmp_path / "projects.json").exists()  # 成功并入的才删


def test_survives_invalid_flat_settings(tmp_path):
    """旧扁平 lumi.json 类型非法：settings 回落默认，其它分区照常并入，不抛异常。"""
    (tmp_path / "lumi.json").write_text(
        json.dumps({"max_checkpoints": "twenty"}), encoding="utf-8"
    )
    (tmp_path / "channels.json").write_text(
        json.dumps({"feishu": {"enabled": True}}), encoding="utf-8"
    )
    migrate_config.migrate_user_store(tmp_path)
    merged = json.loads((tmp_path / "lumi.json").read_text("utf-8"))
    assert merged["channels"] == {"feishu": {"enabled": True}}
    assert "settings" not in merged  # 非法 settings 未并入


def test_nothing_to_migrate(tmp_path):
    assert migrate_config.migrate_user_store(tmp_path) is None
    assert not (tmp_path / "lumi.json").exists()


# ── 项目级：config.yaml → config.json ──


def test_migrates_project_yaml_to_json(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "style: code\nenv:\n  FOO: bar\n", encoding="utf-8"
    )
    assert migrate_config.migrate_project_config(tmp_path) is not None
    data = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert data == {"style": "code", "env": {"FOO": "bar"}}
    assert not (tmp_path / "config.yaml").exists()


def test_project_json_preferred_yaml_untouched(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"style": "a"}), encoding="utf-8")
    (tmp_path / "config.yaml").write_text("style: b\n", encoding="utf-8")
    assert migrate_config.migrate_project_config(tmp_path) is None  # json 已在
    assert (tmp_path / "config.yaml").exists()


def test_project_nothing_to_migrate(tmp_path):
    assert migrate_config.migrate_project_config(tmp_path) is None

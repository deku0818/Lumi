"""LumiConfig 主配置加载测试（读 config.json）。"""

from __future__ import annotations

import json

from lumi.utils.config.manager import LumiConfig


def test_loads_config_json(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"style": "code", "env": {"FOO": "bar"}}), encoding="utf-8"
    )
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config.style == "code"
    assert cfg.config.env == {"FOO": "bar"}


def test_missing_returns_defaults(tmp_path):
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config.style == "default"


def test_ignores_legacy_yaml(tmp_path):
    """config.yaml 不再被读取（迁移交给 scripts/migrate_config.py）。"""
    (tmp_path / "config.yaml").write_text("style: code\n", encoding="utf-8")
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config.style == "default"  # 未读 yaml

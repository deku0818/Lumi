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


def _write_prompt(cfg: LumiConfig, name: str, text: str) -> None:
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    (cfg.prompts_dir / f"{name}.md").write_text(text, encoding="utf-8")


def test_load_prompt_falls_back_to_builtin(tmp_path):
    """未配置 .lumi/prompts/SUMMARY.md 时取框架内置——否则压缩直接报错。"""
    text = LumiConfig(str(tmp_path)).load_prompt("SUMMARY")
    assert text and "摘要" in text


def test_load_prompt_prefers_user_over_builtin(tmp_path):
    """用户自定义压过内置，顺序写反即在此暴露。"""
    cfg = LumiConfig(str(tmp_path))
    _write_prompt(cfg, "SUMMARY", "我的摘要指令")
    assert cfg.load_prompt("SUMMARY") == "我的摘要指令"


def test_load_prompt_skips_empty_file(tmp_path):
    """空文件（含只剩 frontmatter）等同于没有，继续往下找。"""
    cfg = LumiConfig(str(tmp_path))
    _write_prompt(cfg, "SUMMARY", "---\nname: summary\n---\n")
    text = cfg.load_prompt("SUMMARY")
    assert text and "摘要" in text


def test_load_prompt_returns_none_when_nowhere(tmp_path):
    """三层都没有才 None。"""
    assert LumiConfig(str(tmp_path)).load_prompt("NO_SUCH_PROMPT") is None


def test_system_prompt_concats_soul_and_agents(tmp_path):
    """SOUL + AGENTS 按序拼接；无配置时空串（以无系统提示词运行）。"""
    cfg = LumiConfig(str(tmp_path))
    assert cfg.load_system_prompt() == ""  # default 风格无内置 prompts
    _write_prompt(cfg, "SOUL", "灵魂")
    _write_prompt(cfg, "AGENTS", "规则")
    assert cfg.load_system_prompt() == "灵魂\n\n规则"

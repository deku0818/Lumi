"""LumiConfig 主配置加载测试（读 config.json）。"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.styles import STYLES_ROOT
from lumi.utils.config.manager import LumiConfig
from lumi.utils.config.models import Config


def test_loads_config_json(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"env": {"FOO": "bar"}}), encoding="utf-8"
    )
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config.env == {"FOO": "bar"}


def test_missing_returns_defaults(tmp_path):
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config == Config()


def test_ignores_legacy_yaml(tmp_path):
    """config.yaml 不再被读取。"""
    (tmp_path / "config.yaml").write_text("env:\n  FOO: bar\n", encoding="utf-8")
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config == Config()  # 未读 yaml


def _write_prompt(cfg: LumiConfig, name: str, text: str) -> None:
    cfg.prompts_dir.mkdir(parents=True, exist_ok=True)
    (cfg.prompts_dir / f"{name}.md").write_text(text, encoding="utf-8")


def test_load_compact_prompt_from_builtin(tmp_path):
    """未配置压缩提示词时取统一的内置版本。"""
    cfg = LumiConfig(str(tmp_path))
    resolved = cfg.resolve_prompt("COMPACT")
    assert resolved is not None
    source, path, _ = resolved
    assert source == "builtin"
    assert path == STYLES_ROOT / "prompts" / "COMPACT.md"
    text = cfg.load_prompt("COMPACT")
    assert text and "摘要" in text


def test_load_prompt_prefers_user_over_builtin(tmp_path):
    """用户自定义压过内置，顺序写反即在此暴露。"""
    cfg = LumiConfig(str(tmp_path))
    _write_prompt(cfg, "COMPACT", "我的摘要指令")
    assert cfg.load_prompt("COMPACT") == "我的摘要指令"


def test_load_prompt_prefers_project_over_global_and_builtin(tmp_path):
    cfg = LumiConfig(str(tmp_path / "global"))
    _write_prompt(cfg, "COMPACT", "全局摘要指令")
    project = tmp_path / "project"
    prompts = project / ".lumi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "COMPACT.md").write_text("项目摘要指令", encoding="utf-8")
    assert cfg.load_prompt("COMPACT", project) == "项目摘要指令"
    (prompts / "COMPACT.md").unlink()
    assert cfg.load_prompt("COMPACT", project) == "全局摘要指令"
    (cfg.prompts_dir / "COMPACT.md").unlink()
    assert cfg.resolve_prompt("COMPACT", project)[1] == (
        STYLES_ROOT / "prompts" / "COMPACT.md"
    )


def test_load_prompt_skips_empty_file(tmp_path):
    """空文件（含只剩 frontmatter）等同于没有，继续往下找。"""
    cfg = LumiConfig(str(tmp_path))
    _write_prompt(cfg, "COMPACT", "---\nname: compact\n---\n")
    text = cfg.load_prompt("COMPACT")
    assert text and "摘要" in text


def test_load_prompt_returns_none_when_nowhere(tmp_path):
    """各层都没有才 None。"""
    assert LumiConfig(str(tmp_path)).load_prompt("NO_SUCH_PROMPT") is None


def test_system_prompt_only_loads_soul(tmp_path, monkeypatch):
    """各层旧 AGENTS 提示词及项目说明都不自动加载；SOUL 仍按层覆盖。"""
    cfg = LumiConfig(str(tmp_path / "global"))
    assert cfg.load_system_prompt() == ""  # 仅内置压缩提示词
    project = tmp_path / "project"
    prompts = project / ".lumi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "AGENTS.md").write_text("旧项目规则", encoding="utf-8")
    (project / "AGENTS.md").write_text("按需读取的项目说明", encoding="utf-8")
    _write_prompt(cfg, "AGENTS", "旧全局规则")
    builtin = tmp_path / "builtin"
    (builtin / "prompts").mkdir(parents=True)
    (builtin / "prompts" / "AGENTS.md").write_text("旧内置规则", encoding="utf-8")
    monkeypatch.setattr("lumi.styles.STYLES_ROOT", builtin)
    assert cfg.load_system_prompt(project) == ""
    _write_prompt(cfg, "SOUL", "灵魂")
    assert cfg.load_system_prompt(project) == "灵魂"
    (prompts / "SOUL.md").write_text("项目灵魂", encoding="utf-8")
    assert cfg.load_system_prompt(project) == "项目灵魂"


def test_toolbox_is_machine_level(tmp_path, monkeypatch):
    """没显式指定配置目录时，工具箱恒落 ~/.lumi——它是机器级的，不随 cwd 漂移。

    漂了的话，在任何带 `.lumi/` 的项目里启动的 serve / headless / CLI 各看到一个空
    工具箱，桌面端刚装好的工具在那儿显示成「缺失」，用户只会重装第二份到没人用的地方。
    配置层本身仍按发现链走（项目的 config.json / prompts 该被发现）。
    """
    project = tmp_path / "proj"
    (project / ".lumi").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("LUMI_CONFIG_DIR", raising=False)
    monkeypatch.chdir(project)

    cfg = LumiConfig()
    assert cfg.config_dir == project / ".lumi"
    assert cfg.bin_dir == tmp_path / "home" / ".lumi" / "bin"


def test_explicit_config_dir_owns_toolbox(tmp_path):
    """显式指定（构造参数 / LUMI_CONFIG_DIR）时工具箱跟着它——容器与测试靠这条隔离。"""
    assert LumiConfig(str(tmp_path)).bin_dir == tmp_path / "bin"


def test_invalid_section_drops_only_that_section(tmp_path):
    # 某一段校验失败只丢该段，其余配置仍生效。
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "token": {"compact_threshold": 0.5},
                "env": {"FOO": "bar"},
                "agents": "oops",
            }
        ),
        encoding="utf-8",
    )
    cfg = LumiConfig(str(tmp_path))
    assert cfg.config.token.compact_threshold == 0.5
    assert cfg.config.env == {"FOO": "bar"}
    assert cfg.config.agents == type(cfg.config.agents)()  # 坏段回默认

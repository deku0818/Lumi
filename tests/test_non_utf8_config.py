"""非 UTF-8 文件（Windows 记事本另存为 GBK 等）不能让加载整条链崩掉：跳过坏文件。"""

from __future__ import annotations

from lumi.agents.tools.loader import _parse_md_file
from lumi.agents.tools.providers.mcp.config import _read_json_dict
from lumi.utils.config.manager import LumiConfig, project_style_override


def _gbk(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("gbk"))
    return path


def test_skill_file(tmp_path):
    f = _gbk(tmp_path / "SKILL.md", "---\nname: 技能\ndescription: 中文\n---\n正文")
    assert _parse_md_file(str(f)) is None


def test_mcp_config(tmp_path):
    assert _read_json_dict(_gbk(tmp_path / "mcp_server.json", '{"名": {}}')) == {}


def test_project_style(tmp_path):
    _gbk(tmp_path / ".lumi" / "config.json", '{"style": "代码"}')
    assert project_style_override(tmp_path) is None


def test_prompt_layers(isolated_config, tmp_path):
    _gbk(tmp_path / ".lumi" / "prompts" / "SOUL.md", "灵魂")
    LumiConfig.get_instance().load_system_prompt(tmp_path)  # 不抛即可


def test_config_dir_env_expands_tilde(monkeypatch, tmp_path):
    # 回归：LUMI_CONFIG_DIR='~/x' 时 lumi_home 展开到家目录，发现链却落在 <cwd>/~/x，
    # 密钥与工具箱各在一处
    from lumi.utils.config.discovery import ConfigDiscovery
    from lumi.utils.paths import lumi_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LUMI_CONFIG_DIR", "~/lumi-data")
    assert ConfigDiscovery().discover() == lumi_home() == tmp_path / "lumi-data"

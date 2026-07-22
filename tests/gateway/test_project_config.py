"""project_config：项目主页资源聚合的读写删测试。"""

from pathlib import Path

import pytest

from lumi.gateway import project_config
from lumi.styles import STYLES_ROOT
from lumi.utils.config.manager import LumiConfig


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path_factory, monkeypatch):
    """全局层钉到空目录：本机 ~/.lumi 的 prompts/skills 不得泄漏进层序断言。"""
    monkeypatch.setenv("LUMI_CONFIG_DIR", str(tmp_path_factory.mktemp("lumi-home")))
    LumiConfig.reset_instance()
    yield
    LumiConfig.reset_instance()


@pytest.fixture
def builtin_agent() -> str:
    """default 风格内置 agent 名（文件 stem）。"""
    return next(f.stem for f in (STYLES_ROOT / "default" / "agents").glob("*.md"))


def agent_md(name: str, desc: str = "描述") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n提示词"


def skill_md(name: str, desc: str = "描述") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n正文"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """带一个项目层 skill 和 agent 的项目目录。"""
    skills = tmp_path / ".lumi" / "skills" / "my-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        skill_md("my-skill", "项目技能"),
        encoding="utf-8",
    )
    (skills / "references").mkdir()
    (skills / "references" / "extra.md").write_text("ref", encoding="utf-8")
    agents = tmp_path / ".lumi" / "agents"
    agents.mkdir(parents=True)
    (agents / "my-agent.md").write_text(
        "---\nname: my-agent\ndescription: 项目代理\ntools: read\n---\n提示词",
        encoding="utf-8",
    )
    return tmp_path


def test_overview_merges_layers(project: Path):
    ov = project_config.overview(project)
    skills = {s["name"]: s for s in ov["skills"]}
    agents = {a["name"]: a for a in ov["agents"]}
    assert skills["my-skill"]["builtin"] is False
    assert agents["my-agent"]["builtin"] is False
    assert agents["my-agent"]["tools"] == ["read"]
    # default 风格内置 agents（explore / general-purpose）应并入且标记内置
    assert any(a["builtin"] for a in ov["agents"])
    assert [p["name"] for p in ov["prompts"]] == ["SOUL", "AGENTS"]


def test_project_agent_overrides_builtin(project: Path, builtin_agent: str):
    (project / ".lumi" / "agents" / f"{builtin_agent}.md").write_text(
        agent_md(builtin_agent, "覆盖版"), encoding="utf-8"
    )
    agents = {a["name"]: a for a in project_config.overview(project)["agents"]}
    assert agents[builtin_agent]["builtin"] is False
    assert agents[builtin_agent]["description"] == "覆盖版"


def test_read_skill_lists_files(project: Path):
    result = project_config.read_resource(project, "skill", "my-skill")
    assert result["files"][0] == "SKILL.md"
    assert "references/extra.md" in result["files"]
    assert result["builtin"] is False
    assert "正文" in result["content"]
    ref = project_config.read_resource(
        project, "skill", "my-skill", "references/extra.md"
    )
    assert ref["content"] == "ref"


def test_read_rejects_path_escape(project: Path):
    with pytest.raises(ValueError):
        project_config.read_resource(project, "skill", "my-skill", "../../evil.md")
    with pytest.raises(ValueError):
        project_config.read_resource(project, "skill", "../evil", "SKILL.md")
    with pytest.raises(ValueError):
        project_config.read_resource(project, "memory", "../../../etc/passwd")


def test_write_creates_and_updates(project: Path):
    project_config.write_resource(
        project, "skill", "new-skill", skill_md("new-skill", "新")
    )
    assert (project / ".lumi" / "skills" / "new-skill" / "SKILL.md").exists()
    project_config.write_resource(project, "agent", "new-agent", agent_md("new-agent"))
    assert "new-agent" in (project / ".lumi" / "agents" / "new-agent.md").read_text()
    # 提示词是自由文本，不做 frontmatter 校验
    project_config.write_resource(project, "prompt", "SOUL", "灵魂")
    assert (project / ".lumi" / "prompts" / "SOUL.md").read_text() == "灵魂"
    with pytest.raises(ValueError):
        project_config.write_resource(project, "prompt", "EVIL", "x")


def test_write_validates_definition(project: Path):
    # 缺 frontmatter：loader 会静默跳过成幽灵文件，写入时就该拦住
    with pytest.raises(ValueError):
        project_config.write_resource(project, "agent", "ghost", "没有 frontmatter")
    # name 与资源名不一致：UI 按文件身份、运行时按 frontmatter name，必须一致
    with pytest.raises(ValueError):
        project_config.write_resource(project, "agent", "foo", agent_md("bar"))
    with pytest.raises(ValueError):
        project_config.write_resource(project, "skill", "foo", "---\n---\n空元数据")
    # 技能的 references 文件是自由文本，不校验
    project_config.write_resource(
        project, "skill", "my-skill", "任意内容", file="references/note.md"
    )


def test_write_via_symlinked_project_path(project: Path, tmp_path_factory):
    # macOS /tmp → /private/tmp 一类：经 symlink 访问项目根，写入与回显路径都应正常
    link = tmp_path_factory.mktemp("links") / "proj-link"
    link.symlink_to(project)
    result = project_config.write_resource(
        project=link,
        kind="skill",
        name="sym-skill",
        content=skill_md("sym-skill"),
    )
    assert result["ok"] is True
    assert result["path"] == ".lumi/skills/sym-skill/SKILL.md"


def test_delete_project_layer_only(project: Path, builtin_agent: str):
    result = project_config.delete_resource(project, "skill", "my-skill")
    assert result["restored_builtin"] is False
    assert not (project / ".lumi" / "skills" / "my-skill").exists()
    # 内置 agent 未复制到项目层时不可删
    with pytest.raises(ValueError):
        project_config.delete_resource(project, "agent", builtin_agent)


def test_copy_builtin_then_delete_restores(project: Path, builtin_agent: str):
    project_config.copy_builtin(project, "agent", builtin_agent)
    assert (project / ".lumi" / "agents" / f"{builtin_agent}.md").exists()
    agents = {a["name"]: a for a in project_config.overview(project)["agents"]}
    assert agents[builtin_agent]["builtin"] is False
    # 复制后再删 = 恢复内置
    result = project_config.delete_resource(project, "agent", builtin_agent)
    assert result["restored_builtin"] is True
    agents = {a["name"]: a for a in project_config.overview(project)["agents"]}
    assert agents[builtin_agent]["builtin"] is True


def test_copy_builtin_skill_tolerates_leftover_dir(
    project: Path, tmp_path_factory, monkeypatch
):
    # 伪造 style 内置技能（monkeypatch STYLES_ROOT 指向临时树，不碰仓库源码）
    import lumi.styles

    styles_root = tmp_path_factory.mktemp("styles")
    fake = styles_root / "default" / "skills" / "fake-builtin"
    fake.mkdir(parents=True)
    (fake / "SKILL.md").write_text(skill_md("fake-builtin"), encoding="utf-8")
    monkeypatch.setattr(lumi.styles, "STYLES_ROOT", styles_root)
    # 项目层残留同名目录但无 SKILL.md（写 references 可合法造出）
    leftover = project / ".lumi" / "skills" / "fake-builtin"
    (leftover / "references").mkdir(parents=True)
    (leftover / "references" / "r.md").write_text("残留", encoding="utf-8")
    result = project_config.copy_builtin(project, "skill", "fake-builtin")
    assert result["ok"] is True
    assert (leftover / "SKILL.md").exists()


def test_read_returns_stripped_body(project: Path):
    result = project_config.read_resource(project, "skill", "my-skill")
    assert result["body"] == "正文"
    assert result["content"].startswith("---")


def test_prompt_overview_body_strips_frontmatter(project: Path):
    # 卡片预览用 body：用户习惯给 SOUL/AGENTS 加 frontmatter，不应渲染进预览
    project_config.write_resource(
        project, "prompt", "SOUL", "---\ntitle: 灵魂\n---\n正文"
    )
    soul = next(
        p for p in project_config.overview(project)["prompts"] if p["name"] == "SOUL"
    )
    assert soul["body"] == "正文"
    assert soul["content"].startswith("---")


def test_prompt_chain_project_over_style(project: Path):
    import json

    (project / ".lumi" / "config.json").write_text(
        json.dumps({"style": "code"}), encoding="utf-8"
    )
    # code 风格自带 SOUL.md → source=style
    ov = project_config.overview(project)
    soul = next(p for p in ov["prompts"] if p["name"] == "SOUL")
    assert soul["source"] == "style"
    # 项目层写入后覆盖
    project_config.write_resource(project, "prompt", "SOUL", "项目灵魂")
    ov = project_config.overview(project)
    soul = next(p for p in ov["prompts"] if p["name"] == "SOUL")
    assert soul["source"] == "project"
    assert soul["content"] == "项目灵魂"

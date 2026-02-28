"""Agent/Skill 配置解析测试"""

import pytest

from lumi.agents.tools.config import (
    AgentConfig,
    SkillConfig,
    _parse_md_file,
    load_agents,
    load_skills,
)


@pytest.fixture
def sample_agent_md(tmp_path):
    content = """---
name: test-agent
description: A test agent
model: gpt-4
tools: read, write, bash
---
You are a test agent.
"""
    f = tmp_path / "test-agent.md"
    f.write_text(content)
    return f


def test_parse_md_valid(sample_agent_md):
    result = _parse_md_file(str(sample_agent_md))
    assert result is not None
    assert result["name"] == "test-agent"
    assert result["description"] == "A test agent"
    assert result["model"] == "gpt-4"
    assert result["prompt"] == "You are a test agent."


def test_parse_md_tools_as_csv(sample_agent_md):
    result = _parse_md_file(str(sample_agent_md))
    assert result["tools"] == ["read", "write", "bash"]


def test_parse_md_tools_as_list(tmp_path):
    content = """---
name: agent2
description: desc
tools: [read, write]
---
prompt
"""
    f = tmp_path / "agent2.md"
    f.write_text(content)
    result = _parse_md_file(str(f))
    assert result["tools"] == ["read", "write"]


def test_parse_md_no_frontmatter(tmp_path):
    f = tmp_path / "no_fm.md"
    f.write_text("Just plain markdown content")
    result = _parse_md_file(str(f))
    assert result is None


def test_parse_md_invalid_yaml(tmp_path):
    content = """---
name: [invalid yaml
  broken: {
---
prompt
"""
    f = tmp_path / "bad.md"
    f.write_text(content)
    result = _parse_md_file(str(f))
    assert result is None


def test_load_agents_from_directory(tmp_path):
    for name in ["alpha", "beta"]:
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {name} desc\n---\nprompt for {name}"
        )
    agents = load_agents(directory=str(tmp_path))
    assert len(agents) == 2
    names = {a.name for a in agents}
    assert names == {"alpha", "beta"}
    assert all(isinstance(a, AgentConfig) for a in agents)


def test_load_agents_filter_by_name(tmp_path):
    for name in ["alpha", "beta"]:
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: desc\n---\nprompt"
        )
    agents = load_agents(name="alpha", directory=str(tmp_path))
    assert len(agents) == 1
    assert agents[0].name == "alpha"


def test_load_skills_directory_format(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A skill\n---\nDo something"
    )
    skills = load_skills(directory=str(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "my-skill"
    assert isinstance(skills[0], SkillConfig)


def test_load_skills_missing_skill_md(tmp_path):
    # 无 SKILL.md 的目录被跳过
    (tmp_path / "empty-skill").mkdir()
    skills = load_skills(directory=str(tmp_path))
    assert len(skills) == 0

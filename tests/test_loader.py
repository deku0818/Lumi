"""loader 对坏配置文件的容错：单个非法文件跳过并告警，不炸掉整个加载。

回归背景：SkillConfig/AgentConfig 构造抛 pydantic ValidationError（如 frontmatter
里 name 为未加引号的数字，yaml 解析成 int）曾未被捕获，异常穿透 detector.peek()
→ context_inject_hook → 被 dispatch 吞掉，导致整轮上下文注入静默失效且每轮复现。
"""

from __future__ import annotations

from pathlib import Path

from lumi.agents.tools.loader import _load_agents_from_dir, _load_skills_from_dir


def _write_skill(base: Path, dirname: str, frontmatter: str) -> None:
    skill_dir = base / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n正文", encoding="utf-8"
    )


def test_invalid_skill_skipped_not_raised(tmp_path):
    _write_skill(tmp_path, "bad", "name: 2024\ndescription: 数字名")  # int name
    _write_skill(tmp_path, "good", "name: good\ndescription: 正常")
    result = _load_skills_from_dir(tmp_path)
    assert set(result) == {"good"}  # 坏文件跳过，其余正常加载


def test_invalid_agent_skipped_not_raised(tmp_path):
    (tmp_path / "bad.md").write_text(
        "---\nname: 3.14\ndescription: 数字名\n---\n提示词", encoding="utf-8"
    )
    (tmp_path / "good.md").write_text(
        "---\nname: good\ndescription: 正常\n---\n提示词", encoding="utf-8"
    )
    result = _load_agents_from_dir(tmp_path)
    assert set(result) == {"good"}


def test_project_layer_merges_and_overrides(tmp_path):
    """项目 .lumi/ 是最高层：新增并入、同名覆盖；不传 project_dir 则不加载。"""
    from lumi.agents.tools.loader import load_agents, load_skills

    agents_dir = tmp_path / ".lumi" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "proj-agent.md").write_text(
        "---\nname: proj-agent\ndescription: 项目层\n---\n提示词", encoding="utf-8"
    )
    _write_skill(
        tmp_path / ".lumi" / "skills",
        "proj-skill",
        "name: proj-skill\ndescription: 项目层",
    )

    assert any(a.name == "proj-agent" for a in load_agents(project_dir=tmp_path))
    assert any(s.name == "proj-skill" for s in load_skills(project_dir=tmp_path))
    # 无项目层时不可见（进程级两层行为不变）
    assert not any(a.name == "proj-agent" for a in load_agents())
    assert not any(s.name == "proj-skill" for s in load_skills())


def test_load_prompt_project_layer(tmp_path):
    """load_prompt 的项目层优先于其余各层。"""
    from lumi.utils.read_config import get_config

    prompts_dir = tmp_path / ".lumi" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "SOUL.md").write_text("项目灵魂", encoding="utf-8")
    assert get_config().load_prompt("SOUL", tmp_path) == "项目灵魂"

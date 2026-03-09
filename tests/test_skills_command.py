"""/skills 内置命令单元测试

验证 /skills 命令的注册、技能列表展示和空列表提示。
"""

from __future__ import annotations

from lumi.agents.tools.config import SkillConfig
from lumi.tui.slash_commands.handlers import build_skills_output
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.registry import CommandRegistry


# --- 需求 5.1: /skills 注册为内置命令 ---


async def test_skills_command_registration() -> None:
    """/skills 可注册为内置命令，名称为 'skills'，描述为 '查看所有可用技能'"""
    registry = CommandRegistry()
    command = SlashCommand(
        name="skills",
        description="查看所有可用技能",
        command_type=CommandType.BUILTIN,
        handler=build_skills_output,
    )

    assert registry.register(command) is True

    retrieved = registry.get("skills")
    assert retrieved is not None
    assert retrieved.name == "skills"
    assert retrieved.description == "查看所有可用技能"
    assert retrieved.command_type == CommandType.BUILTIN


# --- 需求 5.2: 有技能时展示技能名称和描述列表 ---


def test_skills_command_shows_skill_list() -> None:
    """有技能时，展示每个技能的名称和 token 数"""
    skills = [
        SkillConfig(name="media-digest", description="媒体摘要技能", prompt="摘要"),
        SkillConfig(name="code-review", description="代码审查技能", prompt="审查"),
    ]

    text = build_skills_output(skills, "/fake/skills")
    content = str(text)

    assert "Skills" in content
    assert "2 skills" in content
    assert "media-digest" in content
    assert "code-review" in content
    assert "description tokens" in content


# --- 需求 5.3: 技能列表为空时展示 "暂无可用技能" ---


def test_skills_command_shows_empty_message() -> None:
    """技能列表为空时，展示 '0 skills'"""
    text = build_skills_output([], "/fake/skills")
    content = str(text)

    assert "Skills" in content
    assert "0 skills" in content

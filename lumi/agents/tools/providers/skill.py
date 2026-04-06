"""Skill 工具提供者 - 提供基于提示词模板的技能工具

技能目录结构:
- .skills/skill_name/SKILL.md    主配置文件
- .skills/skill_name/*.md        辅助文档
- .skills/skill_name/scripts/    可执行脚本
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.loader import SkillConfig, _parse_md_file, load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


def _get_skills_root() -> Path:
    """获取 skills 根目录。"""
    return get_config().skills_dir


def _find_skill_source_dir(skill_name: str) -> Path | None:
    """根据 skill 名称在 skills 根目录下查找对应的源目录。

    遍历每个子目录的 SKILL.md，匹配 name 字段。
    未找到返回 None。
    """
    skills_root = _get_skills_root()
    if not skills_root.exists():
        return None

    for skill_dir in skills_root.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        config = _parse_md_file(str(skill_file))
        if config and config.get("name") == skill_name:
            return skill_dir

    return None


def _get_skill_execution_config() -> dict[str, bool | float | int]:
    """获取技能嵌入式命令的执行配置。

    返回包含 enabled、timeout、max_output_bytes 的字典，
    配置加载失败时回退到默认值。
    """
    defaults: dict[str, bool | float | int] = {
        "enabled": True,
        "timeout": 10.0,
        "max_output_bytes": 10_000,
    }

    try:
        app_config = get_config().config
        if hasattr(app_config, "skill_execution"):
            se = app_config.skill_execution
            return {
                "enabled": se.enabled,
                "timeout": se.command_timeout,
                "max_output_bytes": se.max_output_bytes,
            }
    except (AttributeError, TypeError) as e:
        logger.warning("无法加载技能执行配置,使用默认值: %s", e)

    return defaults


async def _execute_embedded_commands(
    prompt_content: str, source_dir: Path, skill_name: str
) -> str:
    """若提示词中包含嵌入式命令且执行功能已启用，则执行并替换。"""
    exec_config = _get_skill_execution_config()
    if not exec_config["enabled"]:
        return prompt_content

    from lumi.agents.tools.providers.skill_executor import SkillCommandExecutor

    if not SkillCommandExecutor.has_commands(prompt_content):
        return prompt_content

    executor = SkillCommandExecutor(
        working_dir=str(source_dir),
        skill_name=skill_name,
        timeout=float(exec_config["timeout"]),
        max_output_bytes=int(exec_config["max_output_bytes"]),
    )
    return await executor.execute_commands(prompt_content)


_SKILL_DESCRIPTION = """Execute skills in the main conversation

When a user asks you to perform a task, check if any available skills match it. Skills provide specialized functionality and domain knowledge.

When a user mentions a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Please use this tool to invoke that skill.

How to invoke:

- Use this tool and specify the skill name
- Example: `skill: "pdf"` — invokes the pdf skill

Important notes:

- Available skills are listed in the `<system-reminder>` within the conversation
- When a user's request matches a skill, it is mandatory: you must call the relevant skill tool before generating any other response for that task
- Do not call a skill that is already running
- Do not use this tool for built-in CLI system commands of type `<command-type>:system` (e.g., /skills, /mcp, /clear, etc.)
- If you see a `<command-name>` tag in the current conversation turn, it means the skill is already loaded — follow the `<skill-content>` instructions directly and do not call this tool again"""


class SkillInput(BaseModel):
    """Skill 工具的输入参数"""

    name: str = Field(description="技能名称")


@tool(description=_SKILL_DESCRIPTION, args_schema=SkillInput)
async def skill(name: str) -> str:
    """根据名称查找并返回对应的技能提示词。"""
    matched_skills: list[SkillConfig] = load_skills(name=name)
    if not matched_skills:
        return f"技能 '{name}' 不存在，请检查技能名称是否正确"

    skill_config = matched_skills[0]
    prompt_content = skill_config.prompt

    # 查找源目录并尝试执行嵌入式命令
    source_dir = _find_skill_source_dir(skill_config.name)
    if source_dir:
        try:
            prompt_content = await _execute_embedded_commands(
                prompt_content, source_dir, skill_config.name
            )
        except Exception as e:
            logger.error("执行技能命令失败: %s", e)

    # 返回 prompt + Tips
    skill_path = str(source_dir) if source_dir else f"skills/{skill_config.name}"
    tips = f"\n\n---\n**Tips**: 技能资源位于 `{skill_path}/` 目录下。"

    return prompt_content + tips

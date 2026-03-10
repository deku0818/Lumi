"""Skill工具提供者 - 提供基于提示词模板的技能工具

该模块支持多文件技能:
- 技能目录结构: .skills/skill_name/SKILL.md
- 辅助文档: .skills/skill_name/*.md
- 可执行脚本: .skills/skill_name/scripts/

使用技能时，直接从本地 skills 目录读取，无需沙箱同步。
"""

from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.config import load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


def _get_skills_root() -> Path:
    """获取 skills 根目录"""
    return get_config().skills_dir


def _get_skill_source_dir(skill_name: str) -> Path | None:
    """根据 skill 名称找到源目录

    Args:
        skill_name: 技能名称

    Returns:
        源目录路径，未找到返回 None
    """
    from lumi.agents.tools.config import _parse_md_file

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


def _get_skill_execution_config() -> dict:
    """获取技能命令执行配置"""
    defaults = {
        "enabled": True,
        "timeout": 10.0,
        "max_output_bytes": 10_000,
    }

    try:
        config = get_config().config

        if hasattr(config, "skill_execution"):
            se_config = config.skill_execution
            return {
                "enabled": se_config.enabled,
                "timeout": se_config.command_timeout,
                "max_output_bytes": se_config.max_output_bytes,
            }
    except Exception as e:
        logger.warning(f"无法加载技能执行配置,使用默认值: {e}")

    return defaults


_SKILL_DESCRIPTION = """Execute skills in the main conversation

When a user asks you to perform a task, check if any available skills match. Skills provide specialized functionality and domain knowledge.  
When a user mentions a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Please use this tool to invoke that skill.

How to invoke:  
- Use this tool and specify the skill name and optional parameters  
- Example: `skill: "pdf"` — invokes the pdf skill  

Important notes:  
- Available skills are listed in the <system-reminder> within the conversation  
- When a user's request matches a skill, it is mandatory: you must invoke the relevant skill tool before generating any other response about that task  
- Never mention a skill without actually invoking this tool  
- Do not invoke a skill that is already running  
- Do not use this tool for built-in CLI system commands of type <command-type>:system (e.g., /skills, /help, /clear, etc.)  
- If you see a <command-name> tag in the current conversation turn, it means the skill has already been loaded — follow the <skill-content> directly and do not invoke this tool again"""


class SkillInput(BaseModel):
    """Skill工具的输入参数"""

    name: str = Field(description="技能名称")


@tool(description=_SKILL_DESCRIPTION, args_schema=SkillInput)
async def skill(name: str) -> str:
    """Skill工具 - 根据名称返回对应的技能提示词"""
    skill_configs = load_skills(name=name)
    if not skill_configs:
        return f"技能 '{name}' 不存在，请检查技能名称是否正确"

    skill_config = skill_configs[0]
    prompt_content = skill_config.prompt

    # 找到源目录
    source_dir = _get_skill_source_dir(skill_config.name)
    if source_dir:
        # 执行嵌入式命令(如果启用且存在)
        exec_config = _get_skill_execution_config()

        if exec_config["enabled"]:
            try:
                from lumi.agents.tools.providers.skill_executor import (
                    SkillCommandExecutor,
                )

                if SkillCommandExecutor.has_commands(prompt_content):
                    executor = SkillCommandExecutor(
                        working_dir=str(source_dir),
                        skill_name=skill_config.name,
                        timeout=exec_config["timeout"],
                        max_output_bytes=exec_config["max_output_bytes"],
                    )
                    prompt_content = await executor.execute_commands(prompt_content)
            except Exception as e:
                logger.error(f"执行技能命令失败: {e}")

    # 返回 prompt + Tips
    skill_path = str(source_dir) if source_dir else f"skills/{skill_config.name}"
    tips = f"\n\n---\n**Tips**: 技能资源位于 `{skill_path}/` 目录下。"

    return prompt_content + tips

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


def _create_skill_schema():
    """动态创建skill工具的schema"""
    skills = load_skills()
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": [skill.name for skill in skills],
                "description": "技能名称",
            }
        },
        "required": ["name"],
    }

    # 构建技能列表
    skill_list = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)

    description = f"""在主对话中执行一个技能。
<skills_instructions>
当用户要求你执行任务时，检查下方可用技能列表中是否有技能可以更有效地帮助完成该任务。技能提供了专门的能力和领域知识。

调用方式：
- 使用此工具时仅传入技能名称
- 示例：`name: "故障码查询"` —— 调用故障码查询技能

重要事项：
- 当某个技能与用户任务相关时，应优先调用此工具获取技能指导
- 只能使用在下面 <available_skills> 中列出的技能
- 技能资源位于本地 skills 目录下
</skills_instructions>

<available_skills>
{skill_list}
</available_skills>"""

    return description, schema


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


class SkillInput(BaseModel):
    """Skill工具的输入参数"""

    name: str = Field(description="技能名称")


@tool(description=_create_skill_schema()[0], args_schema=SkillInput)
async def skill(name: str) -> str:
    """Skill工具 - 根据名称返回对应的技能提示词"""
    skill_configs = load_skills(name=name)
    if not skill_configs:
        return f"Skill '{name}' not found"

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

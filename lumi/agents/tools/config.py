"""配置加载模块 - 解析MD文件的Agent和Skill配置"""

import glob
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


class AgentConfig(BaseModel):
    """Agent配置模型"""

    name: str = Field(description="代理名称")
    description: str = Field(description="代理描述")
    model: str | None = Field(default=None, description="指定使用的模型名称")
    tools: list[str] = Field(default_factory=list, description="代理使用的工具列表")
    system_prompt: str = Field(description="代理的系统提示词")


class SkillConfig(BaseModel):
    """Skill配置模型"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    prompt: str = Field(description="技能的提示词")


def _parse_md_file(file_path: str) -> dict | None:
    """
    解析MD文件,提取YAML前置元数据和内容

    Args:
        file_path: MD文件路径

    Returns:
        dict | None: 包含 name, description, tools, prompt 的字典
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # 分离YAML前置元数据和提示词
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                yaml_content = parts[1].strip()
                prompt = parts[2].strip()
            else:
                logger.warning(f"文件格式不正确: {file_path}")
                return None
        else:
            logger.warning(f"文件缺少YAML前置元数据: {file_path}")
            return None

        # 解析YAML元数据
        try:
            metadata = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            logger.error(f"解析YAML失败 {file_path}: {e}")
            return None

        # 处理tools字段
        tools_data = metadata.get("tools", [])
        if isinstance(tools_data, str):
            tools_data = [tool.strip() for tool in tools_data.split(",")]

        return {
            "name": metadata.get("name", ""),
            "description": metadata.get("description", ""),
            "model": metadata.get("model"),
            "tools": tools_data,
            "prompt": prompt,
        }

    except Exception as e:
        logger.error(f"处理文件失败 {file_path}: {e}")
        return None


def load_agents(
    name: str | None = None, directory: str | None = None
) -> list[AgentConfig]:
    """
    从配置目录加载agent配置

    Args:
        name: 可选的代理名称过滤
        directory: agent配置目录，如果为None则从配置获取

    Returns:
        List[AgentConfig]: agent配置列表
    """
    if directory is None:
        directory = str(get_config().agents_dir)

    agents = []
    md_files = glob.glob(os.path.join(directory, "*.md"))

    for file_path in md_files:
        config_dict = _parse_md_file(file_path)
        if config_dict is None:
            continue

        agent_config = AgentConfig(
            name=config_dict["name"],
            description=config_dict["description"],
            model=config_dict.get("model"),
            tools=config_dict["tools"],
            system_prompt=config_dict["prompt"],
        )
        agents.append(agent_config)

    if name is not None:
        agents = [agent for agent in agents if agent.name == name]

    return agents


def load_skills(
    name: str | None = None, directory: str | None = None
) -> list[SkillConfig]:
    """
    从配置目录加载skill配置

    支持目录格式: .skills/skill_name/SKILL.md
    每个技能是一个包含 SKILL.md 的子目录

    Args:
        name: 可选的技能名称过滤
        directory: skill配置目录，如果为None则从配置获取

    Returns:
        List[SkillConfig]: skill配置列表
    """
    if directory is None:
        directory = str(get_config().skills_dir)

    skills = []
    base_path = Path(directory)

    if not base_path.exists():
        return skills

    # 扫描子目录中的 SKILL.md
    for skill_dir in base_path.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        config_dict = _parse_md_file(str(skill_file))
        if config_dict is None:
            continue

        skill_config = SkillConfig(
            name=config_dict["name"],
            description=config_dict["description"],
            prompt=config_dict["prompt"],
        )
        skills.append(skill_config)

    if name is not None:
        skills = [skill for skill in skills if skill.name == name]

    return skills

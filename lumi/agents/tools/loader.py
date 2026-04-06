"""配置加载 — 从 Markdown 文件解析 Agent 和 Skill 配置。

文件格式: YAML frontmatter (``---`` 分隔) + Markdown 正文作为 prompt。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


# ------------------------------------------------------------------
# 数据模型
# ------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Agent 配置。"""

    name: str = Field(description="代理名称")
    description: str = Field(description="代理描述")
    model: str | None = Field(default=None, description="指定使用的模型名称")
    tools: list[str] = Field(default_factory=list, description="代理使用的工具列表")
    system_prompt: str = Field(description="代理的系统提示词")


class SkillConfig(BaseModel):
    """Skill 配置。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    prompt: str = Field(description="技能的提示词")


# ------------------------------------------------------------------
# Markdown 文件解析
# ------------------------------------------------------------------


def _parse_md_file(file_path: str) -> dict[str, object] | None:
    """解析带 YAML frontmatter 的 Markdown 文件。

    Returns:
        包含 ``name``, ``description``, ``model``, ``tools``, ``prompt``,
        ``raw_metadata`` 的字典；解析失败返回 ``None``。
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"读取文件失败 {file_path}: {e}")
        return None

    # 分离 YAML frontmatter 和正文
    if not content.startswith("---"):
        logger.warning(f"文件缺少 YAML frontmatter: {file_path}")
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        logger.warning(f"文件格式不正确: {file_path}")
        return None

    try:
        metadata: dict = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError as e:
        logger.error(f"解析 YAML 失败 {file_path}: {e}")
        return None

    # tools 支持 CSV 字符串和列表两种写法
    tools_raw = metadata.get("tools", [])
    tools = (
        [t.strip() for t in tools_raw.split(",")]
        if isinstance(tools_raw, str)
        else tools_raw
    )

    return {
        "name": metadata.get("name", ""),
        "description": metadata.get("description", ""),
        "model": metadata.get("model"),
        "tools": tools,
        "prompt": parts[2].strip(),
        "raw_metadata": metadata,
    }


# ------------------------------------------------------------------
# Agent 加载
# ------------------------------------------------------------------


def _load_agents_from_dir(directory: Path) -> dict[str, AgentConfig]:
    """从目录中加载所有 ``*.md`` 文件为 AgentConfig。"""
    result: dict[str, AgentConfig] = {}
    for md_file in directory.glob("*.md"):
        parsed = _parse_md_file(str(md_file))
        if parsed is None:
            continue
        try:
            cfg = AgentConfig(
                name=parsed["name"],
                description=parsed["description"],
                model=parsed.get("model"),
                tools=parsed["tools"],
                system_prompt=parsed["prompt"],
            )
        except (KeyError, TypeError) as e:
            logger.error(f"Agent 配置不完整，跳过 {md_file}: {e}")
            continue
        result[cfg.name] = cfg
    return result


def load_agents(
    name: str | None = None,
    directory: str | None = None,
) -> list[AgentConfig]:
    """加载 Agent 配置。

    加载优先级: 风格内置 agents → 用户 ``.lumi/agents/`` (同名覆盖)。

    Args:
        name: 只返回指定名称的 agent。
        directory: 用户 agent 目录，默认从全局配置获取。
    """
    config = get_config()
    merged: dict[str, AgentConfig] = {}

    # 1) 风格内置 agents
    style = config.active_style
    from lumi.styles import get_style_agents_dir

    try:
        style_dir = get_style_agents_dir(style)
        merged = _load_agents_from_dir(Path(style_dir))
        if merged:
            logger.info(
                f"从风格 '{style}' 加载了 {len(merged)} 个内置 agent: "
                f"{', '.join(merged.keys())}"
            )
    except ValueError as e:
        logger.warning(f"加载风格 '{style}' agents 失败: {e}")

    # 2) 用户 agents（同名覆盖风格内置）
    user_dir = Path(directory) if directory else config.agents_dir
    for agent_name, agent_cfg in _load_agents_from_dir(user_dir).items():
        if agent_name in merged:
            logger.warning(
                f"用户 agent '{agent_name}' 覆盖了风格 '{style}' 的内置同名 agent"
            )
        merged[agent_name] = agent_cfg

    agents = list(merged.values())
    if name is not None:
        agents = [a for a in agents if a.name == name]
    return agents


# ------------------------------------------------------------------
# Skill 加载
# ------------------------------------------------------------------


def load_skills(
    name: str | None = None,
    directory: str | None = None,
) -> list[SkillConfig]:
    """加载 Skill 配置。

    目录结构: ``<skills_dir>/<skill_name>/SKILL.md``

    Args:
        name: 只返回指定名称的 skill。
        directory: skill 根目录，默认从全局配置获取。
    """
    base = Path(directory) if directory else Path(str(get_config().skills_dir))
    if not base.exists():
        return []

    skills: list[SkillConfig] = []
    for skill_dir in base.iterdir():
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue

        parsed = _parse_md_file(str(skill_file))
        if parsed is None:
            continue

        skills.append(
            SkillConfig(
                name=parsed["name"],
                description=parsed["description"],
                prompt=parsed["prompt"],
            )
        )

    if name is not None:
        skills = [s for s in skills if s.name == name]
    return skills

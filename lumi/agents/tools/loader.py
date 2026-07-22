"""配置加载 — 从 Markdown 文件解析 Agent 和 Skill 配置。

文件格式: YAML frontmatter (``---`` 分隔) + Markdown 正文作为 prompt。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from lumi.utils.config.manager import parse_frontmatter
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
    path: str | None = Field(default=None, description="定义文件路径（自改静默判定用）")


class SkillConfig(BaseModel):
    """Skill 配置。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    prompt: str = Field(description="技能的提示词")
    path: str | None = Field(default=None, description="定义文件路径（自改静默判定用）")


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

    # 分离 YAML frontmatter 和正文（与记忆索引规范化共用 parse_frontmatter）
    metadata, body = parse_frontmatter(content)
    if not metadata:
        logger.warning(f"文件缺少有效 YAML frontmatter: {file_path}")
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
        "prompt": body,
        "raw_metadata": metadata,
    }


# ------------------------------------------------------------------
# 配置层序（skills/agents 共用的单一事实源；prompts 的对应物是 manager.prompt_layers）
# ------------------------------------------------------------------


def config_layers(
    subdir: str, project_dir: str | Path | None = None
) -> list[tuple[str, Path]]:
    """(来源标签, 目录) 列表，优先级从低到高，逐层同名覆盖。

    style 内置（builtin）→ 进程配置目录（global）→ 项目 ``.lumi/``（project，
    仅在传入 project_dir 时存在）。load_skills/load_agents、detector 的变更扫描、
    gateway/project_config 的 UI 聚合都消费这一份——层序只写在这里。
    """
    from lumi.styles import STYLES_ROOT

    config = get_config()
    style = config.active_style_for(project_dir)
    layers = [
        ("builtin", STYLES_ROOT / style / subdir),
        ("global", config.config_dir / subdir),
    ]
    if project_dir:
        layers.append(("project", Path(project_dir) / ".lumi" / subdir))
    return layers


def validate_definition(content: str, name: str) -> None:
    """技能/Agent 定义文件的落盘前校验——与本模块的加载要求对齐。

    加载侧对缺 frontmatter / 缺 name 的文件静默跳过（_parse_md_file）；写入侧
    不拦住的话，文件会「写成功却在列表里消失」成为无删除入口的幽灵。name 必须
    与目录名/文件名一致：UI 与 CRUD 按文件身份定位，运行时按 frontmatter name
    归并覆盖，两者一致才不会出现展示与加载背离。
    """
    metadata, _ = parse_frontmatter(content)
    if not metadata or not metadata.get("name") or not metadata.get("description"):
        raise ValueError("frontmatter 需包含 name 与 description")
    if metadata["name"] != name:
        raise ValueError(f"frontmatter 的 name 需与名称一致: {name}")


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
                path=str(md_file),
            )
        except (KeyError, TypeError, ValidationError) as e:
            logger.error(f"Agent 配置不完整，跳过 {md_file}: {e}")
            continue
        result[cfg.name] = cfg
    return result


def load_agents(
    name: str | None = None,
    directory: str | None = None,
    project_dir: str | Path | None = None,
) -> list[AgentConfig]:
    """加载 Agent 配置。

    加载优先级: 风格内置 agents → 进程配置 ``.lumi/agents/`` → 项目 ``.lumi/agents/``
    (逐层同名覆盖)。项目层随会话绑定的项目传入——不传则保持进程级两层（TUI /
    无项目场景）。

    Args:
        name: 只返回指定名称的 agent。
        directory: 覆盖进程配置层目录（测试用），默认从全局配置获取。
        project_dir: 会话绑定的项目根，其 ``.lumi/agents/`` 为最高层。
    """
    merged: dict[str, AgentConfig] = {}
    for label, layer_dir in config_layers("agents", project_dir):
        if label == "global" and directory:
            layer_dir = Path(directory)
        merged |= _load_agents_from_dir(layer_dir)

    agents = list(merged.values())
    if name is not None:
        agents = [a for a in agents if a.name == name]
    return agents


# ------------------------------------------------------------------
# Skill 加载
# ------------------------------------------------------------------


def _load_skills_from_dir(directory: Path) -> dict[str, SkillConfig]:
    """从目录中加载所有 ``<skill_name>/SKILL.md`` 为 SkillConfig。"""
    result: dict[str, SkillConfig] = {}
    if not directory.exists():
        return result
    for skill_dir in directory.iterdir():
        skill_file = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_file.exists():
            continue
        parsed = _parse_md_file(str(skill_file))
        if parsed is None:
            continue
        try:
            cfg = SkillConfig(
                name=parsed["name"],
                description=parsed["description"],
                prompt=parsed["prompt"],
                path=str(skill_file),
            )
        except (KeyError, ValidationError) as e:
            logger.error(f"Skill 配置不完整，跳过 {skill_file}: {e}")
            continue
        result[cfg.name] = cfg
    return result


def load_skills(
    name: str | None = None,
    directory: str | None = None,
    project_dir: str | Path | None = None,
) -> list[SkillConfig]:
    """加载 Skill 配置。

    加载优先级: 风格内置 skills → 进程配置 ``.lumi/skills/`` → 项目 ``.lumi/skills/``
    (逐层同名覆盖)。目录结构: ``<skills_dir>/<skill_name>/SKILL.md``

    Args:
        name: 只返回指定名称的 skill。
        directory: 覆盖进程配置层目录（测试用），默认从全局配置获取。
        project_dir: 会话绑定的项目根，其 ``.lumi/skills/`` 为最高层。
    """
    merged: dict[str, SkillConfig] = {}
    for label, layer_dir in config_layers("skills", project_dir):
        if label == "global" and directory:
            layer_dir = Path(directory)
        merged |= _load_skills_from_dir(layer_dir)

    skills = list(merged.values())
    if name is not None:
        skills = [s for s in skills if s.name == name]
    return skills

"""Skill 工具提供者 - 提供基于提示词模板的技能工具

技能目录结构:
- .skills/skill_name/SKILL.md    主配置文件
- .skills/skill_name/*.md        辅助文档
- .skills/skill_name/scripts/    可执行脚本
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.loader import SkillConfig, _parse_md_file, load_skills
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# ============================================================================
# Skill Command Executor
# ============================================================================


class SkillCommandExecutor:
    """执行技能 markdown 内容中的嵌入式命令。"""

    # 匹配 !`command` 或 !```command``` 语法
    COMMAND_PATTERN = re.compile(r"!```(.+?)```|!`([^`]+)`", re.MULTILINE | re.DOTALL)

    def __init__(
        self,
        working_dir: str,
        skill_name: str,
        timeout: float = 10.0,
        max_output_bytes: int = 10_000,
    ) -> None:
        self.working_dir = working_dir
        self.skill_name = skill_name
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    async def execute_commands(self, content: str) -> str:
        """执行内容中的所有嵌入式命令，将成功的命令替换为其输出。

        失败或超时的命令保留原始文本不变。
        """
        rendered = content
        for match in self.COMMAND_PATTERN.finditer(content):
            command = (match.group(1) or match.group(2)).strip()
            original_text = match.group(0)

            command_output = await self._run_single_command(command)
            if command_output is not None:
                rendered = rendered.replace(original_text, command_output, 1)

        return rendered

    async def _run_single_command(self, command: str) -> str | None:
        """执行单个命令，成功返回截断后的 stdout，失败返回 None。"""
        logger.debug("执行技能命令: %s", command)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except TimeoutError:
            logger.warning("技能命令执行超时: %s", command)
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except (ProcessLookupError, OSError):
                pass
            return None
        except OSError as e:
            logger.error("执行技能命令时发生异常 '%s': %s", command, e)
            return None

        if proc.returncode != 0:
            error_msg = (
                stderr_bytes.decode("utf-8", errors="ignore")
                or f"退出码: {proc.returncode}"
            )
            logger.warning("技能命令执行失败: %s - %s", command, error_msg)
            return None

        output = stdout_bytes.decode("utf-8", errors="ignore")
        if len(output.encode()) > self.max_output_bytes:
            output = output.encode()[: self.max_output_bytes].decode(errors="ignore")
        return output

    @staticmethod
    def has_commands(content: str) -> bool:
        """检查内容是否包含 !`command` 形式的可执行命令。"""
        return SkillCommandExecutor.COMMAND_PATTERN.search(content) is not None


# ============================================================================
# Skill Provider
# ============================================================================


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

    if not SkillCommandExecutor.has_commands(prompt_content):
        return prompt_content

    executor = SkillCommandExecutor(
        working_dir=str(source_dir),
        skill_name=skill_name,
        timeout=float(exec_config["timeout"]),
        max_output_bytes=int(exec_config["max_output_bytes"]),
    )
    return await executor.execute_commands(prompt_content)


_SKILL_DESCRIPTION = """在主对话中执行技能（skill）。

当用户要求执行某项任务时，先检查可用技能里有没有匹配的。技能提供专门的能力与领域知识。

当用户提到"斜杠命令"或 "/<某命令>"（如 "/commit"、"/review-pr"）时，指的就是技能，请用本工具调用它。

如何调用：
- 用本工具并指定技能名称，如 `name: "pdf"` 调用 pdf 技能

注意事项：
- 可用技能列表在对话中的 `<system-reminder>` 里给出
- 用户请求与某技能匹配时，这是强制要求：必须先调用该技能，再生成关于此任务的其它回应
- 不要调用已在运行中的技能
- 不要用本工具执行系统命令（如 /stop、/clear、/help）——它们由渠道层直接处理，不是技能
- 若当前对话回合已出现 `<command-name>` 标签，说明技能已加载——直接按 `<skill-content>` 的指示执行，不要再调用本工具"""


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

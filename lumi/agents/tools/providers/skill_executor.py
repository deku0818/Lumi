"""Skill command executor - 在本地执行技能中的嵌入式命令

支持 !`command` 和 !```command``` 语法，
在技能目录中执行命令，失败时保留原始文本并记录日志。
"""

from __future__ import annotations

import asyncio
import re

from lumi.utils.logger import logger


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
        except asyncio.TimeoutError:
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

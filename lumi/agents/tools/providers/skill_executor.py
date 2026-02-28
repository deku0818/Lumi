"""Skill command executor - 在本地执行技能中的嵌入式命令

该模块为技能提供嵌入式命令执行能力:
- 支持 !`command` 和 !```command``` 语法
- 在本地技能目录中执行
- 失败时静默跳过并记录日志
"""

import asyncio
import re

from lumi.utils.logger import logger


class SkillCommandExecutor:
    """执行技能 markdown 内容中的嵌入式命令"""

    # Regex pattern to match !`command` or !```command```
    COMMAND_PATTERN = re.compile(r"!```(.+?)```|!`([^`]+)`", re.MULTILINE | re.DOTALL)

    def __init__(
        self,
        working_dir: str,
        skill_name: str,
        timeout: float = 10.0,
        max_output_bytes: int = 10_000,
    ):
        """初始化命令执行器

        Args:
            working_dir: 命令执行的工作目录（技能目录路径）
            skill_name: 技能名称
            timeout: 命令超时时间(秒),默认 10 秒
            max_output_bytes: 最大输出字节数,默认 10KB
        """
        self.working_dir = working_dir
        self.skill_name = skill_name
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    async def execute_commands(self, content: str) -> str:
        """执行内容中的所有嵌入式命令并替换为输出

        Args:
            content: 包含 !`command` 语法的技能提示词内容

        Returns:
            命令被替换为输出后的内容
        """
        result_content = content
        for match in self.COMMAND_PATTERN.finditer(content):
            command = (match.group(1) or match.group(2)).strip()
            original_text = match.group(0)

            logger.debug(f"执行技能命令: {command}")

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

                if proc.returncode == 0:
                    output = stdout_bytes.decode("utf-8", errors="ignore")
                    # 截断输出
                    if len(output.encode()) > self.max_output_bytes:
                        output = output.encode()[: self.max_output_bytes].decode(
                            errors="ignore"
                        )
                    result_content = result_content.replace(original_text, output, 1)
                else:
                    error_msg = (
                        stderr_bytes.decode("utf-8", errors="ignore")
                        or f"退出码: {proc.returncode}"
                    )
                    logger.warning(f"技能命令执行失败: {command} - {error_msg}")
                    # 保留原始命令文本

            except asyncio.TimeoutError:
                logger.warning(f"技能命令执行超时: {command}")
                try:
                    proc.kill()
                except (ProcessLookupError, UnboundLocalError):
                    pass
            except Exception as e:
                logger.error(f"执行技能命令时发生异常 '{command}': {e}")
                # 保留原始命令文本

        return result_content

    @staticmethod
    def has_commands(content: str) -> bool:
        """检查内容是否包含可执行命令

        Args:
            content: 要检查的内容

        Returns:
            如果内容包含 !`command` 模式则返回 True
        """
        return SkillCommandExecutor.COMMAND_PATTERN.search(content) is not None

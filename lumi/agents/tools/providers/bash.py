"""Bash 工具提供者 - 提供本地 shell 命令执行功能

该模块为 AI 代理提供 shell 命令执行能力:
- 本地持久化 shell 会话
- 持久化会话，保持环境变量、别名、工作目录等状态
- 超时和输出限制
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.session import CommandResult, get_session_manager
from lumi.agents.tools.workspace import get_authorized_directory
from lumi.utils.logger import logger


def _format_result(result: CommandResult) -> str:
    """格式化执行结果"""
    if result.success:
        return result.stdout or "<no output>"
    if result.timed_out:
        return "Error: Timeout"
    output = f"Error: Exit code {result.exit_code}"
    if result.stdout:
        output += f"\n{result.stdout}"
    return output


class BashInput(BaseModel):
    """Bash 命令执行的输入参数"""

    command: str = Field(description="要执行的 shell 命令")


@tool(args_schema=BashInput)
async def bash(command: str) -> str:
    """在本地环境中执行非交互式 shell 命令。

    环境: 工作目录为当前项目目录，会话状态持久化（cd、export、alias 等）。
    限制: 不支持交互式命令（python、vim、ssh 等无参数调用会超时）。
    用法:
    可执行所有的非交互式命令
    - 如: 执行 Python 代码:
    ```
    python << 'EOF'
    print("Hello, World!")
    EOF
    ```
    - 当有需求时请执行`date`获取当前时间"""
    try:
        working_dir = str(get_authorized_directory())
        session = get_session_manager().get_session(
            thread_id="default",
            working_dir=working_dir,
        )
        result = await session.execute(command)
        return _format_result(result)

    except Exception as e:
        logger.error(f"[bash] 执行失败: {e}")
        return f"执行失败: {e}"

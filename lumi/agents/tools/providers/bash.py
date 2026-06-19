"""Bash 工具提供者 - 提供本地 shell 命令执行功能

持久化 shell 会话，保持环境变量、别名、工作目录等状态，
支持超时控制和后台执行。
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.permissions.workspace import get_authorized_directory
from lumi.agents.runtime.shell_session import CommandResult, get_shell_session_manager
from lumi.utils.logger import logger


def _format_result(result: CommandResult) -> str:
    """将命令执行结果格式化为用户可读的字符串。"""
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
    description: str = Field(description="命令用途描述，帮助理解命令意图")
    timeout: float = Field(default=120.0, ge=1, le=600, description="超时秒数")
    run_in_background: bool = Field(default=False, description="是否后台执行")


@tool(args_schema=BashInput)
async def bash(
    command: str,
    description: str,
    timeout: float = 120.0,
    run_in_background: bool = False,
) -> str:
    """**Executes a given bash command and returns its output.**

    The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

    **IMPORTANT**: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool:

    - File search: Use **Glob** (NOT find or ls)
    - Content search: Use **Grep** (NOT grep or rg)
    - Read files: Use **Read** (NOT cat/head/tail)
    - Edit files: Use **Edit** (NOT sed/awk)
    - Write files: Use **Write** (NOT echo >/cat <<EOF)
    - Communication: Output text directly (NOT echo/printf)

    ## Parameters

    | 参数 | 类型 | 必填 | 说明 |
    |------|------|------|------|
    | `command` | string | 是 | 要执行的命令 |
    | `description` | string | 是 | 命令的简明描述 |
    | `timeout` | number | 否 | 超时时间（秒），最大 600（10分钟），默认 120（2分钟） |
    | `run_in_background` | boolean | 否 | 设为 true 可在后台运行，完成后会收到通知 |

    ## 关键规则

    - 文件路径包含空格时用双引号括起来
    - 尽量使用绝对路径，避免 `cd`
    - 独立命令可以并行调用多个 Bash 工具
    - 依赖前一个命令结果的用 `&&` 串联
    - 后台任务用 `run_in_background`，不需要加 `&`；启动后等通知即可，**不要**反复查状态或读输出文件
    - 避免不必要的 `sleep`
    - Git 操作有严格的安全协议（不强制推送、不跳过 hooks、不 amend 除非明确要求等）
    """
    try:
        working_dir = str(get_authorized_directory())
        session_mgr = get_shell_session_manager()
        session = session_mgr.get_session(thread_id="default", working_dir=working_dir)

        if run_in_background:
            current_cwd = await session.get_cwd()
            task = await session_mgr.bg_manager.start_task(
                command=command,
                timeout=timeout,
                working_dir=current_cwd,
            )
            return (
                f"后台任务已启动\n"
                f"Task ID: {task.task_id}\n"
                f"Output File: {task.output_file.resolve()}\n"
                f"\n"
                f"完成时你会自动收到通知。在此之前**不要**轮询状态或读取 Output File，"
                f"等通知即可——期间请继续做别的事。\n"
            )

        command_result = await session.execute(command, timeout=timeout)
        return _format_result(command_result)

    except OSError as e:
        logger.error("[bash] 系统错误: %s", e, exc_info=True)
        return f"系统错误（进程/文件操作失败）: {e}"
    except Exception as e:
        logger.error("[bash] 未预期的错误: %s", e, exc_info=True)
        return f"执行失败（内部错误）: {e}"

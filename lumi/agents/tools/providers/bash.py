"""Bash 工具提供者 - 提供本地 shell 命令执行功能

持久化 shell 会话，保持环境变量、工作目录等状态，
支持超时控制和后台执行。
"""

from __future__ import annotations

from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

from lumi.agents.runtime.bg_process import get_bg_manager
from lumi.agents.runtime.bg_tasks import current_thread_id
from lumi.agents.runtime.shell_session import (
    CommandResult,
    current_shell_key,
    get_shell_session_manager,
)
from lumi.agents.runtime.workspace import get_project_directory
from lumi.agents.tools.shell_syntax import has_background_operator
from lumi.utils.constants import DEFAULT_COMMAND_TIMEOUT
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
    timeout: float = Field(
        default=DEFAULT_COMMAND_TIMEOUT,
        gt=0,
        le=600,
        description="前台命令超时秒数，默认 180 秒，最大 600 秒；后台任务不限时",
    )
    run_in_background: bool = Field(
        default=False,
        description="是否后台执行，true为后台运行，不受timeout超时限制，完成后会自动收到通知",
    )


BASH_DESCRIPTION = """执行 bash 命令返回标准输出和标准错误。工作目录与环境变量在调用间保持。shell 非交互，不读取 rc / profile，继承服务进程环境；stdin 接 /dev/null，不支持交互输入。

## 文件操作与搜索
- 查找文件、搜索内容优先通过bash使用 rg。
- 读、改、写文件分别优先用 read、edit、write。

## 执行规则
- 路径含空格时加引号；优先使用绝对路径，避免不必要的 cd。
- 命令中禁止使用后台符 &，后台执行用 run_in_background=true。

## 前台与后台
- 需要命令结果才能继续时用前台；有独立工作可并行时，用 run_in_background=true。
- 后台启动后返回 task_id 和输出文件路径，完成时自动收到 <task-notification>。
- 等通知期间继续其他工作，不主动轮询状态或读取输出文件；用户明确询问进度时才查询。通知到达前如实说明仍在运行，不编造结果。
"""


@tool(args_schema=BashInput, description=BASH_DESCRIPTION)
async def bash(
    command: str,
    description: str,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    run_in_background: bool = False,
) -> str:
    """执行 shell 命令并返回输出（持久化 shell 会话 / 超时 / 后台执行）。

    前台 timeout 默认 180 秒，范围 (0, 600]；后台任务不限时。
    """
    try:
        working_dir = str(get_project_directory())
        session_mgr = get_shell_session_manager()
        # shell 会话键：子代理用其专属 key（run_with_shell 注入，与父/兄弟隔离、用完回收），
        # 否则用本会话 thread。共用 "default" 时并发会话/子代理的 cd 会互相污染、相对路径
        # 跑到别处。
        shell_key = current_shell_key() or current_thread_id.get() or "default"
        session = session_mgr.get_session(thread_id=shell_key, working_dir=working_dir)

        if run_in_background:
            # 命令自带 & 时，被追踪的 wrapper shell 会 fork 后立即退出（任务瞬间被误报
            # 完成），真实进程脱管——完成时收不到通知、也无法取消。不静默剥掉，报错让
            # 模型改写命令。
            if has_background_operator(command):
                return (
                    "Error: 命令包含 shell 后台符 `&`，与 run_in_background 叠加时"
                    "被追踪的进程会立即退出、真实进程脱管（完成时收不到通知，也无法"
                    "取消）。请去掉 `&`（及配套的 `echo $!` 等），由 run_in_background "
                    "追踪完整生命周期。"
                )
            current_cwd = session.cwd
            task = await get_bg_manager().start_task(
                command=command,
                timeout=None,
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
        if command_result.timed_out:
            raise ToolException(f"Error: Timeout（超过 {timeout:g} 秒，命令已终止）")
        return _format_result(command_result)

    except ToolException:
        raise
    except OSError as e:
        logger.error("[bash] 系统错误: %s", e, exc_info=True)
        return f"系统错误（进程/文件操作失败）: {e}"
    except Exception as e:
        logger.error("[bash] 未预期的错误: %s", e, exc_info=True)
        return f"执行失败（内部错误）: {e}"

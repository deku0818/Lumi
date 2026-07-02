"""Bash 工具提供者 - 提供本地 shell 命令执行功能

持久化 shell 会话，保持环境变量、别名、工作目录等状态，
支持超时控制和后台执行。
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.permissions.workspace import get_authorized_directory
from lumi.agents.runtime.bg_tasks import current_thread_id
from lumi.agents.runtime.shell_session import (
    CommandResult,
    current_shell_key,
    get_shell_session_manager,
)
from lumi.agents.tools.capability import has_background_operator
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
    timeout: float | None = Field(
        default=None,
        ge=0,
        le=600,
        description="超时秒数；0 表示不限时（仅后台可用，前台传 0 报错）。省略时前台默认 120s、后台不限时",
    )
    run_in_background: bool = Field(default=False, description="是否后台执行")


# 工具描述。长行用行尾反斜杠续行拆开（模块级常量，续行顶格自然）：源码每行都短、
# 好编辑，拼接后逐字等于一整行，不插入额外换行或空格。
BASH_DESCRIPTION = """**Executes a given bash command and returns its output.**

The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

**IMPORTANT**: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, \
or `echo` commands, unless explicitly instructed or after \
you have verified that a dedicated tool cannot accomplish your task. \
Instead, use the appropriate dedicated tool:

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
| `timeout` | number | 否 | 超时秒数，最大 600（10分钟）；`0`=不限时（仅后台，前台传 0 报错）。省略时前台默认 120（2分钟）、后台不限时 |
| `run_in_background` | boolean | 否 | 设为 true 可在后台运行，完成后会收到通知 |

## 关键规则

- 文件路径包含空格时用双引号括起来
- 尽量使用绝对路径，避免 `cd`
- 独立命令可以并行调用多个 Bash 工具
- 依赖前一个命令结果的用 `&&` 串联
- Git 操作有严格的安全协议（不强制推送、不跳过 hooks、不 amend 除非明确要求等）

## 后台任务
长耗时命令（构建 / 测试 / 下载 / 起服务）用 `run_in_background=True`：
- 后台默认**不限时**；需要墙钟上限再传 `timeout`（起常驻服务保持默认即可，不会被砍）
- 在后台执行后会立即返回 `task_id` 和输出文件路径
- 命令完成时**自动**收到 `<task-notification>` 消息提示
- **不要轮询** `background_task(action="status")`；**不要主动 read output_file**。等通知，期间继续做别的
- 用户明确问进度才查 status；通知到达前**不要编造结果**，如实说任务还在跑
- **前台 vs 后台**：需要结果继续推进 → 前台；有独立工作可并行 → 后台
- 避免不必要的 `sleep`
"""


@tool(args_schema=BashInput, description=BASH_DESCRIPTION)
async def bash(
    command: str,
    description: str,
    timeout: float | None = None,
    run_in_background: bool = False,
) -> str:
    """执行 shell 命令并返回输出（持久化 shell 会话 / 超时 / 后台执行）。

    timeout 语义：0 表示不限时，仅后台可用（前台传 0 报错）。前台省略回落默认
    120s；后台省略即不限时（后台常用于起服务/长跑，默认有界会被误杀）。
    """
    try:
        working_dir = str(get_authorized_directory())
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
            current_cwd = await session.get_cwd()
            # 后台省略(None)或显式 0 → 不限时；否则用给定上限
            bg_timeout = timeout if timeout else None
            task = await session_mgr.bg_manager.start_task(
                command=command,
                timeout=bg_timeout,
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

        # 前台不开放无界阻塞（会永久挂死当前回合且无 task_id 可取消）：
        # 显式 0 报错，省略(None)回落默认 120s
        if timeout == 0:
            return "Error: timeout=0（不限时）仅后台可用；前台请省略或给正数超时"
        fg_timeout = timeout if timeout is not None else 120.0
        command_result = await session.execute(command, timeout=fg_timeout)
        return _format_result(command_result)

    except OSError as e:
        logger.error("[bash] 系统错误: %s", e, exc_info=True)
        return f"系统错误（进程/文件操作失败）: {e}"
    except Exception as e:
        logger.error("[bash] 未预期的错误: %s", e, exc_info=True)
        return f"执行失败（内部错误）: {e}"

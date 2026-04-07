"""后台任务管理工具 — 查询、停止后台运行的 Bash 和 Agent 任务。"""

from __future__ import annotations

import time
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.runtime.task_registry import (
    TaskKind,
    TaskStatus,
    get_task_registry,
)
from lumi.utils.logger import logger


class BackgroundTaskInput(BaseModel):
    """后台任务管理的输入参数"""

    action: Literal["list", "status", "stop"] = Field(
        description="操作类型: list(列出所有后台任务), status(查询指定任务详情), stop(停止运行中的任务)"
    )
    task_id: str | None = Field(
        default=None,
        description="任务 ID（status 和 stop 操作必填）",
    )


BACKGROUND_TASK_DESCRIPTION = """管理后台运行的任务（Bash 命令和 Agent 代理）。

## 操作

| action | task_id | 说明 |
|--------|---------|------|
| `list` | 不需要 | 列出所有后台任务及其状态 |
| `status` | 必填 | 查询指定任务的详细状态和输出文件路径 |
| `stop` | 必填 | 停止运行中的任务 |

## 读取任务输出

使用 `status` 获取 output_file 路径后，用 Read 工具读取输出内容。
"""


@tool(description=BACKGROUND_TASK_DESCRIPTION, args_schema=BackgroundTaskInput)
async def background_task(
    action: Literal["list", "status", "stop"],
    task_id: str | None = None,
) -> str:
    """管理后台任务"""
    match action:
        case "list":
            return _handle_list()
        case "status":
            if not task_id:
                return "错误: status 操作需要提供 task_id"
            return _handle_status(task_id)
        case "stop":
            if not task_id:
                return "错误: stop 操作需要提供 task_id"
            return await _handle_stop(task_id)
        case _:
            return f"未知操作: {action}"


def _handle_list() -> str:
    """列出所有后台任务。"""
    registry = get_task_registry()
    entries = registry.all_tasks()

    if not entries:
        return "当前没有后台任务"

    lines = ["Task ID | Kind | Status | Label | Duration"]
    lines.append("--------|------|--------|-------|--------")

    now = time.time()
    for e in entries:
        elapsed = e.completed_at or now
        duration = int(elapsed - e.started_at)
        lines.append(f"{e.task_id} | {e.kind} | {e.status} | {e.label} | {duration}s")

    return "\n".join(lines)


def _handle_status(task_id: str) -> str:
    """查询指定任务的详细状态。"""
    registry = get_task_registry()
    entry = registry.get(task_id)

    if entry is None:
        return f"任务 {task_id} 不存在"

    now = time.time()
    elapsed = (entry.completed_at or now) - entry.started_at

    lines = [
        f"Task ID: {entry.task_id}",
        f"Kind: {entry.kind}",
        f"Status: {entry.status}",
        f"Label: {entry.label}",
        f"Duration: {int(elapsed)}s",
        f"Output File: {entry.output_file.resolve()}",
    ]

    if entry.agent_name:
        lines.append(f"Agent: {entry.agent_name}")
    if entry.exit_code is not None:
        lines.append(f"Exit Code: {entry.exit_code}")
    if entry.error:
        lines.append(f"Error: {entry.error}")

    if entry.status == TaskStatus.RUNNING:
        lines.append("\n提示: 任务仍在运行中，可用 stop 操作停止。")
    else:
        lines.append("\n提示: 使用 Read 工具读取 Output File 获取完整输出。")

    return "\n".join(lines)


async def _handle_stop(task_id: str) -> str:
    """停止运行中的后台任务。"""
    registry = get_task_registry()
    entry = registry.get(task_id)

    if entry is None:
        return f"任务 {task_id} 不存在"
    if entry.status != TaskStatus.RUNNING:
        return f"任务 {task_id} 状态为 {entry.status}，无法停止"

    if entry.kind == TaskKind.BASH:
        return await _stop_bash_task(task_id)
    elif entry.kind == TaskKind.AGENT:
        return _stop_agent_task(task_id)
    else:
        return f"未知任务类型: {entry.kind}"


async def _stop_bash_task(task_id: str) -> str:
    """停止 Bash 后台任务。"""
    from lumi.agents.tools.runtime.session import get_session_manager

    session_mgr = get_session_manager()
    if not session_mgr.has_bg_manager:
        return f"后台任务管理器未初始化，无法停止任务 {task_id}"

    try:
        await session_mgr.bg_manager.cancel_task(task_id)
    except Exception as e:
        logger.error(
            "[background_task] 停止 Bash 任务失败 %s: %s", task_id, e, exc_info=True
        )
        return f"停止 Bash 任务 {task_id} 失败: {e}"

    logger.info("[background_task] 已停止 Bash 任务 %s", task_id)
    return f"已停止 Bash 任务 {task_id}"


def _stop_agent_task(task_id: str) -> str:
    """停止 Agent 后台任务。"""
    registry = get_task_registry()
    if registry.cancel_agent_task(task_id):
        logger.info("[background_task] 已停止 Agent 任务 %s", task_id)
        return f"已停止 Agent 任务 {task_id}"
    return f"无法停止 Agent 任务 {task_id}（可能已完成或无关联的异步任务）"

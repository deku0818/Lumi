"""Workflow 工具提供者 - 用一段确定性 Python 脚本编排一群子代理。

脚本在受限命名空间执行，通过注入的钩子扇出 LLM 子代理（``agent``），``parallel`` /
``pipeline`` 并发编排，``phase`` / ``log`` 辅助。脚本可内联（``script``）或从文件读入
（``path``，优先）。后台执行：立即返回 task_id，跑完主动通知。
执行引擎见 ``lumi/agents/core/workflow/engine.py``。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

from lumi.agents.core.workflow import (
    WorkflowEngine,
    WorkflowOutcome,
    WorkflowScriptError,
)
from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    get_task_registry,
    make_bg_done_callback,
    run_background_task,
)
from lumi.agents.tools.loader import load_tool_md, require_tool_field

_BG_TASKS_DIR = ".lumi/bg_tasks"
_TASK_ID_HEX_LENGTH = 12

WORKFLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "script": {
            "type": "string",
            "description": (
                "内联编排脚本（Python 代码片段，不要再包 def/async def）。"
                "用 agent()/parallel()/pipeline()/phase()/log() 钩子，"
                "顶层可 await，用 return 给出最终产物。与 path 二选一。"
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "编排脚本文件路径（相对当前工作目录或绝对路径）。提供则**优先于 script**"
                "——把脚本写进文件便于版本化与迭代（改文件后以同 path 重跑）。"
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "工作流简短名称（kebab-case，如 review-auth-changes）；"
                "省略时由 path 文件名推断。"
            ),
        },
        "description": {
            "type": "string",
            "description": "一句话描述这个工作流做什么",
            "default": "",
        },
        "args": {
            "description": "可选输入值，脚本内以全局 args 读取（任意 JSON）",
        },
    },
    "required": [],
}


def _load_description() -> str:
    """从 style MD 加载工具 description，缺失抛 RuntimeError（与 plan 工具一致）。"""
    parsed = load_tool_md("workflow")
    if parsed is None:
        raise RuntimeError(
            "未找到 workflow.md 配置文件。"
            "请确保 style 目录或 .lumi/prompts/tools/ 下存在该文件。"
        )
    return require_tool_field(parsed, "description", "workflow")


_workflow_description = _load_description()


@tool(description=_workflow_description, args_schema=WORKFLOW_SCHEMA)
async def workflow(
    runtime: ToolRuntime,
    script: str | None = None,
    path: str | None = None,
    name: str = "",
    description: str = "",
    args=None,
) -> str:
    """用一段确定性脚本编排子代理，后台执行。"""
    # path 优先于 script：读出版本化脚本文件（可审计 / 可迭代）。
    if path:
        file_path = Path(path).expanduser()
        try:
            script = file_path.read_text(encoding="utf-8")
        except OSError as e:
            return f"workflow 读取脚本文件失败 (path={path}): {e}"
        name = name or file_path.stem
    elif not script:
        return "workflow 需要提供 script（内联脚本）或 path（脚本文件路径）之一。"
    name = name or "workflow"

    # 子代理复用父 PermissionEngine（共享工作区边界）、继承父 tool_mode。
    engine = WorkflowEngine(
        script,
        permission_engine=runtime.context.permission_engine,
        tool_mode=runtime.state.get("tool_mode", "default"),
        args=args,
        name=name,
    )
    try:
        engine.compile()
    except WorkflowScriptError as e:
        return f"workflow 脚本编译失败:\n{e}\n请修正脚本后重试。"

    entry = _start_workflow_task(name, engine)
    desc_line = f"说明: {description}\n" if description else ""
    return (
        f"workflow 已在后台启动\n"
        f"Task ID: {entry.task_id}\n"
        f"Name: {name}\n"
        f"{desc_line}"
        f"Output File: {entry.output_file.resolve()}\n"
        f"完成时你会自动收到 task-notification。"
        f"通知到达前不要查 status、不要读 Output File、不要猜测结果；"
        f"继续做其他事或回答用户。"
    )


# ---------------------------------------------------------------------------
# Background workflow helpers（对齐 agent.py 的后台任务范式）
# ---------------------------------------------------------------------------


def _start_workflow_task(name: str, engine: WorkflowEngine) -> BackgroundTaskEntry:
    """注册后台 Workflow 任务并 fire-and-forget 启动。"""
    from lumi.agents.permissions.workspace import get_authorized_directory

    task_id = f"wf_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"
    output_dir = Path(str(get_authorized_directory())) / _BG_TASKS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{task_id}.json"

    entry = BackgroundTaskEntry(
        task_id=task_id,
        kind=TaskKind.WORKFLOW,
        status=TaskStatus.RUNNING,
        label=f"workflow:{name}",
        started_at=time.time(),
        output_file=output_file,
        agent_name=name,
    )

    registry = get_task_registry()
    registry.register(entry)

    # 绑定进度回调：引擎 phase/agent 起止 → notify_progress → bg_tasks.update 推前端
    engine.set_progress_sink(lambda p: registry.notify_progress(task_id, p))

    async_task = asyncio.create_task(
        _run_workflow_background(task_id, engine, output_file, name)
    )
    entry.async_task = async_task
    async_task.add_done_callback(make_bg_done_callback(task_id, "workflow bg"))
    return entry


def _format_outcome(outcome: WorkflowOutcome, name: str) -> str:
    """把 WorkflowOutcome 序列化为 JSON 文本写入 output_file。"""
    payload = {
        "summary": name,
        "agent_count": outcome.agent_count,
        "logs": outcome.logs,
        "result": outcome.result,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


async def _run_workflow_background(
    task_id: str,
    engine: WorkflowEngine,
    output_file: Path,
    name: str,
) -> None:
    """后台执行 workflow 脚本；收尾（写文件 / 状态 / 通知）走共用 run_background_task。"""
    registry = get_task_registry()

    async def _produce() -> str:
        outcome = await engine.run()
        entry = registry.get(task_id)
        if entry is not None:
            entry.agent_count = outcome.agent_count
        return _format_outcome(outcome, name)

    await run_background_task(
        task_id, output_file, _produce, cancel_text="工作流被取消"
    )

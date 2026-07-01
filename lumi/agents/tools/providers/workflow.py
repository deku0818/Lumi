"""Workflow 工具提供者 - 用一段确定性 Python 脚本编排一群子代理。

脚本在受限命名空间执行，通过注入的钩子扇出 LLM 子代理（``agent``），``parallel`` /
``pipeline`` 并发编排，``phase`` / ``log`` 辅助。脚本可内联（``script``）或从文件读入
（``path``，优先）。后台执行：立即返回 task_id，跑完主动通知。
执行引擎见 ``lumi/agents/core/workflow/engine.py``。
"""

# 注意：本模块**不能**加 `from __future__ import annotations`。它会把 `runtime: ToolRuntime`
# 注解字符串化，导致 langchain 在工具调用时认不出该注入参数、不注入 → "missing runtime"。
# 同 agent.py（见回归测试 test_workflow_runtime_injected_via_toolnode）。

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.core.workflow import (
    WorkflowEngine,
    WorkflowOutcome,
    WorkflowScriptError,
)
from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    bg_tasks_dir,
    get_task_registry,
    make_bg_done_callback,
    run_background_task,
)

_TASK_ID_HEX_LENGTH = 12


class WorkflowInput(BaseModel):
    """Workflow 工具的输入参数"""

    script: str | None = Field(
        default=None,
        description=(
            "内联编排脚本（Python 代码片段，不要再包 def/async def）。"
            "用 agent()/parallel()/pipeline()/phase()/log() 钩子，"
            "顶层可 await，用 return 给出最终产物。与 path 二选一。"
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "编排脚本文件路径（相对当前工作目录或绝对路径）。提供则**优先于 script**"
            "——把脚本写进文件便于版本化与迭代（改文件后以同 path 重跑）。"
        ),
    )
    name: str = Field(
        default="",
        description=(
            "工作流简短名称（kebab-case，如 review-auth-changes）；"
            "省略时由 path 文件名推断。"
        ),
    )
    description: str = Field(default="", description="一句话描述这个工作流做什么")
    args: Any = Field(
        default=None, description="可选输入值，脚本内以全局 args 读取（任意 JSON）"
    )


WORKFLOW_DESCRIPTION = """用一段确定性的 Python 脚本编排一群子代理（多 agent 编排）。

适合三类**结构性困难**：要全面（分解问题并行覆盖）、要有把握（多视角独立验证后再下结论）、规模超出一个上下文（大范围审计 / 迁移 / 扫描）。单点查询、琐碎改动、纯对话**不要**用它。

## 何时允许使用（重要）
本工具会扇出大量子代理、开销很大，**只在用户明确选择编排时才调用**，满足以下任一：
- **Ultra 档位已开启**（你会在对话里收到「Ultra 编排模式已开启」的 system-reminder）；
- **用户用自己的话明确要求**用 workflow / 多 agent 编排（如「用 workflow 审一遍」「并行扇出子代理」）。

否则，即使任务看起来很适合编排，也**不要主动调用**——正常处理任务即可；若任务确实庞大，可一句话建议用户「开启 Ultra 档位后我能并行拆解处理」，由用户决定。

## 执行模型
后台执行：本工具立即返回 task_id，脚本在后台跑完后你会自动收到 task-notification。脚本里的「干活」单元是 `agent()`——派一个独立上下文的 LLM 子代理（语义推理），子代理可用 bash / filesystem 等工具自主完成确定性的活。并发上限约 min(16, CPU-2)，传多少都收，只是排队。

## 脚本怎么写
脚本是一段 Python 代码片段（**不要**再包 `def` / `async def`，引擎会自动包进 async 函数），可直接用顶层 `await` 和 `return`。`return` 的值就是最终产物，形状由你定。两种给法：内联 `script`，或把脚本写进文件、用 `path` 引用（版本化、可复核，**优先于** `script`）。
注入的钩子（只在脚本内可用）：

- `agent(prompt, *, schema=None, label=None, phase=None, agent_name=None)` —— async，派一个 LLM 子代理。给了 `schema`（JSON Schema dict）就强制结构化输出、返回校验过的 dict；否则返回子代理最终文本。\
`agent_name` 指定 .lumi/agents 里的具名子代理，缺省用通用子代理。**注意**：schema 模式下若子代理多次填不对结构会被中止，此时返回 `None`——拿来索引前先判空（`r or {}`、`[x for x in r if x]`）。
- `parallel(thunks)` —— async，**屏障**：并发跑一组无参 thunk（`lambda: agent(...)`），等全部完成才返回列表；失败项落 `None`，用前 `[x for x in r if x]` 过滤。
- `pipeline(items, stage1, stage2, ...)` —— async，**无屏障**：每个 item 独立穿过所有 stage，谁先走完谁先往下。stage 收 `(prev, item, idx)`（按形参个数截取，\
`lambda d: ...` 也行），第一个 stage 的 prev 就是 item。**默认优先用 pipeline**，只有 stage N 真需要 N-1 的全部结果时才用 parallel。
- `phase(title)` / `log(msg)` —— 标记阶段 / 发进度。`args` —— 你传入的输入值。

## 关键规则
- thunk 必须是**无参函数**：`lambda: agent(...)`，不是 `agent(...)`（后者会立即执行，parallel 失去调度权）。
- 脚本本身不能 `import` / 读写文件（它只是编排骨架）；干活靠 `agent()`——确定性的重活让子代理用 bash / filesystem 等工具去做。
- 让结果可信：每条发现派独立 skeptic 用 `schema` 对抗式验证（prompt 里要求"默认证伪，须独立核对源码"）。

## 规范范式（Review：维度→找问题→对抗验证→汇总）
```python
DIMENSIONS = [
    {"key": "security", "prompt": "审查 X 的安全问题，读 a.py b.py，只报有证据的问题"},
    {"key": "correctness", "prompt": "审查 X 的正确性 bug ..."},
]
FINDINGS = {"type": "object", "properties": {"findings": {"type": "array",
    "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "file": {"type": "string"},
        "severity": {"type": "string"}, "detail": {"type": "string"}},
        "required": ["title", "file", "severity", "detail"]}}}, "required": ["findings"]}
VERDICT = {"type": "object", "properties": {
    "is_real": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["is_real", "reason"]}

phase("Review")
reviewed = await pipeline(
    DIMENSIONS,
    lambda d: agent(d["prompt"], schema=FINDINGS, label=d["key"], phase="Review"),
    lambda r: parallel([
        (lambda f=f: agent(
            f"对抗式验证这条发现，默认证伪除非能独立核对源码确认：{f}",
            schema=VERDICT, phase="Verify"))
        for f in (r or {}).get("findings", [])
    ]),
)
confirmed = [f for stage in reviewed if stage
             for f in stage if f and f.get("is_real")]
return {"confirmed": confirmed, "count": len(confirmed)}
```
（注意 `lambda f=f:` 的默认参数绑定，避免闭包都引用最后一个 f。）
"""


@tool(args_schema=WorkflowInput, description=WORKFLOW_DESCRIPTION)
async def workflow(
    runtime: ToolRuntime,
    script: str | None = None,
    path: str | None = None,
    name: str = "",
    description: str = "",
    args=None,
) -> str:
    """用一段确定性脚本编排子代理，后台执行（详见 WORKFLOW_DESCRIPTION）。"""
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
        tool_mode=runtime.context.tool_mode,
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
    task_id = f"wf_{uuid.uuid4().hex[:_TASK_ID_HEX_LENGTH]}"
    output_file = bg_tasks_dir() / f"{task_id}.json"

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

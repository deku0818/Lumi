"""Create, discover and continue persistent subagent sessions; workflow keeps its temporary runner."""

# ToolRuntime must remain a concrete annotation for LangChain's parameter injection.
import asyncio
import json
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from lumi.agents.runtime.bg_tasks import current_thread_id, new_task_id
from lumi.agents.runtime.shell_session import run_with_shell
from lumi.agents.runtime.subagents import get_subagent_service
from lumi.utils.config import get_config


async def create_subagent(
    parent,
    tools: list,
    *,
    system_prompt: str | None = None,
    model_name: str | None = None,
    depth: int = 1,
    interactive: bool = False,
):
    """Workflow 等临时执行单元的构建入口；持久 subagent 由运行时执行器管理。

    复用父 PermissionEngine 与项目根（共享权限规则）；不持久化、不带持久记忆
    （临时执行单元保持上下文干净，AGENTS.md 仍按系统提示词规则按需读取）；<env> 的
    渠道条目随父传播。返回的 run(prompt, schema) 统一构造输入、传播审批上下文，
    并在独立 shell 中执行；可经 on_progress 接收每步状态。
    """
    from lumi.agents.core.graph import create_agent

    lumi_agent, context = await create_agent(
        tools=tools,
        system_prompt=system_prompt,
        model_name=model_name,
        permission_engine=parent.permission_engine,
        project_dir=parent.project_dir,
        enable_memory=False,
    )
    context.env_extra = parent.env_extra
    if interactive:
        context.approval_broker = parent.approval_broker
        context.mode_parent = parent

    async def run(prompt: str, schema: dict | None = None, *, on_progress=None) -> dict:
        inputs = {"messages": [HumanMessage(content=prompt)], "depth": depth}
        if schema is not None:
            inputs["output_schema"] = schema

        async def execute() -> dict:
            if on_progress is None:
                return await lumi_agent.graph.ainvoke(inputs, context=context)
            final: dict = {}
            async for state in lumi_agent.graph.astream(
                inputs, context=context, stream_mode="values"
            ):
                final = state
                on_progress(state)
            return final

        return await run_with_shell(new_task_id("sub-"), execute())

    return run


def _subagent_tools(all_tools: list, subagent_depth: int, max_depth: int) -> list:
    """持久子代理的 ask 由执行器转交主 Agent，不直接向用户发问；
    未达委派上限保留 agent_create / workflow（可继续往下委派），否则剔除以防无限递归。"""
    excluded = {"agent_create", "workflow"} if subagent_depth >= max_depth else set()
    return [t for t in all_tools if t.name not in excluded]


class AgentCreateInput(BaseModel):
    name: str = Field(
        min_length=1,
        description="agent名称；可用 Agent 名称见 <system-reminder>",
    )
    title: str = Field(
        min_length=1,
        description="简短、具体的任务标题，通常为 6–12 个字或 2–5 个单词。",
    )
    cwd: str | None = Field(
        default=None,
        min_length=1,
        description="子代理工作目录；默认继承主会话当前目录，相对路径基于该目录解析，续聊保持不变。",
    )
    prompt: str = Field(min_length=1, description="交给 agent 执行的任务的描述")
    run_in_background: bool = Field(
        default=True,
        description="默认后台执行，完成时推送返回结果，false为前台等待执行完成",
    )


class AgentMessageInput(BaseModel):
    agent_id: str = Field(description="agent_create 或 agent_list 返回的稳定agent ID")
    prompt: str = Field(
        min_length=1, description="发给原子会话的新任务、补查要求或问题的答案"
    )
    run_in_background: bool = Field(
        default=True, description="默认后台执行；已有任务运行时排队，不并发执行两轮"
    )


def _parent_id(runtime: ToolRuntime) -> str:
    config = getattr(runtime, "config", None) or {}
    return config.get("configurable", {}).get("thread_id") or current_thread_id.get()


def _working_directory(runtime: ToolRuntime, cwd: str | None) -> Path:
    context = runtime.context
    project = context.project_dir
    if project is None and context.permission_engine is not None:
        project = context.permission_engine.project_dir
    base = Path(project or Path.cwd()).resolve()
    directory = Path(cwd).expanduser() if cwd is not None else base
    return (base / directory).resolve()


def _request_key(runtime: ToolRuntime, parent_id: str, operation: str) -> str | None:
    call_id = getattr(runtime, "tool_call_id", None)
    return f"{parent_id}:{operation}:{call_id}" if call_id else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


@tool(
    args_schema=AgentCreateInput,
    description="""创建具有独立上下文的子代理，自主完成指定任务。不同代理类型具有不同的能力和可用工具。
子代理无法看到主对话，prompt 必须包含完成任务所需的目标、背景和约束。

执行方式：
- 默认后台运行，立即返回 agent_id，以及本轮 turn_id/task_id；完成后自动通知本轮结果，无需轮询。
- 多个互不依赖的任务应并行创建子代理；等待期间继续处理其他工作。
- 仅当子代理结果是下一步工作的唯一前提，且没有其他工作可做时，设置 run_in_background=false 同步等待。

创建后可通过 agent_message 补充信息或追问。""",
)
async def agent_create(
    prompt: str,
    runtime: ToolRuntime,
    name: str,
    title: str,
    cwd: str | None = None,
    run_in_background: bool = True,
) -> str:
    """Create a named subagent instance; new instance identity is independent of its first task."""
    from lumi.agents.core.preprocessing.agent_detector import AgentChangeDetector

    depth = runtime.state.get("depth", 0)
    maximum = get_config().config.agents.max_delegation_depth
    if depth >= maximum:
        return f"已达到最大委派层数（{maximum}），无法再委派子代理"
    parent_id = _parent_id(runtime)
    try:
        configs = await asyncio.to_thread(
            AgentChangeDetector.get_instance(runtime.context.project_dir).peek
        )
        profile = next((config for config in configs if config.name == name), None)
        if profile is None:
            raise ValueError(
                f"Agent '{name}' 未启用或不可用，请查看 system-reminder 中的可用清单"
            )
        working_dir = _working_directory(runtime, cwd)
        service = get_subagent_service()
        receipt = service.create(
            parent_id,
            str(working_dir),
            profile.model_dump(),
            depth + 1,
            prompt,
            title=title,
            notify=run_in_background,
            request_key=_request_key(runtime, parent_id, "create"),
            env_extra=runtime.context.env_extra,
            tool_mode=runtime.context.tool_mode,
        )
        if not run_in_background:
            receipt = await service.wait(receipt["turn_id"])
        return _json(receipt)
    except (ValueError, RuntimeError) as error:
        return _json({"error": str(error)})


@tool(description="""列出当前会话已创建的子代理""")
async def agent_list(runtime: ToolRuntime) -> str:
    """Read subagents owned by the caller, without invoking a model or restoring a process."""
    parent_id = _parent_id(runtime)
    if not parent_id:
        return _json({"error": "需要当前会话 thread_id"})
    return _json({"agents": get_subagent_service().list_agents(parent_id)})


@tool(
    args_schema=AgentMessageInput,
    description="""向已有子代理发送与其先前任务相关的后续工作、补充信息或问题答复，复用其已有上下文。独立任务应通过 agent_create 创建新代理。
agent_id 必须来自当前会话的 agent_create 或 agent_list。
默认后台运行，完成后自动回传本轮结果，无需轮询。""",
)
async def agent_message(
    agent_id: str,
    prompt: str,
    runtime: ToolRuntime,
    run_in_background: bool = True,
) -> str:
    """Submit another FIFO turn to an owned subagent instance."""
    parent_id = _parent_id(runtime)
    try:
        service = get_subagent_service()
        receipt = service.message(
            parent_id,
            agent_id,
            prompt,
            notify=run_in_background,
            request_key=_request_key(runtime, parent_id, "message"),
        )
        if not run_in_background:
            receipt = await service.wait(receipt["turn_id"])
        return _json(receipt)
    except (ValueError, RuntimeError) as error:
        return _json({"error": str(error)})

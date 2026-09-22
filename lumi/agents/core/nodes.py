from typing import Literal

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeError
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from lumi.agents.core.hooks import HookContext, dispatch_hooks, has_hooks
from lumi.agents.core.meta_message import (
    should_show_human_message,
    visible_user_text,
)
from lumi.agents.core.node_helpers.execution import (
    handle_tool_error,
    truncate_tool_results,
)
from lumi.agents.core.node_helpers.messages import (
    cleanup_incomplete_tool_calls,
    dangling_tool_calls,
    inject_message_cache_breakpoints,
    is_malformed_tool_call,
)
from lumi.agents.core.preprocessing.compact import (
    compact_messages,
    is_circuit_open,
    is_ptl_error,
    record_circuit_failure,
    reset_circuit,
    select_for_ptl_compaction,
)
from lumi.agents.core.response import message_transform
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.core.structured_tool import (
    MAX_CONSECUTIVE_FAILURES,
    STRUCTURED_OUTPUT_INSTRUCTION,
    count_consecutive_structured_output_failures,
    create_structured_output_tool,
    format_structured_output_abort_message,
    is_internal_tool,
)
from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.permissions.routing import route_decision
from lumi.agents.tools.capability import is_local_path_tool, is_write_tool
from lumi.models.chain import structured_output, tool_call_chain
from lumi.models.manager import detect_protocol
from lumi.models.provider_store import resolve, resolve_pointer
from lumi.utils.config import get_config
from lumi.utils.logger import logger
from lumi.utils.sizing import context_window_tokens


async def call_model(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> dict | Command:

    system_prompt = runtime.context.system_prompt
    model_name = runtime.context.model_name
    tools = runtime.context.tools

    # ToolStrategy: output_schema 存在时注入结构化输出真工具（进 ToolExecutor 执行）
    actual_tools = list(tools)
    output_schema = state.get("output_schema")
    if output_schema:
        actual_tools.append(create_structured_output_tool(output_schema))
        system_prompt += STRUCTURED_OUTPUT_INSTRUCTION
        # 不强制 tool_choice：模型自决何时调用，OnAgentStop 的 Stop hook 兜底拉回。
        # 强制 tool_choice="any" 会与 Anthropic thinking 冲突（400）。

    # 输出上限按模型取（用户覆盖 > models.dev 探测），全局 agents.max_tokens 仅作兜底。
    # 给小了不是"省钱"而是把模型的 tool_call 参数从中间切断——截断的 JSON 被
    # 补全解析后会变成缺字段（报错）或半截字符串（静默写坏文件）。
    chain = tool_call_chain(
        actual_tools,
        system_prompt=system_prompt,
        model_name=model_name,
        max_tokens=resolve(model_name, runtime.context.provider).max_tokens
        or get_config().config.agents.max_tokens,
        tool_choice=None,
        apply_effort=True,  # 思考档位只在主对话链生效
        effort=runtime.context.effort,  # 渠道会话的档位覆盖（None=跟随 profile）
        provider=runtime.context.provider,  # 同名模型跨 profile 不串味
    )
    messages = list(state["messages"])

    # Anthropic 模型：为对话消息注入缓存断点（滑动窗口策略）
    if detect_protocol(model_name) == "anthropic":
        inject_message_cache_breakpoints(messages)

    # 多模态 block 内部统一 Anthropic 风格,在此按 provider 转换
    transformed_messages: list = []
    for m in messages:
        if isinstance(m, HumanMessage) and isinstance(m.content, list):
            new_content = await message_transform(m.content, model_name=model_name)
            transformed_messages.append(m.model_copy(update={"content": new_content}))
        else:
            transformed_messages.append(m)

    # 失败不在此拦，交给节点级 error_handler（见 on_call_model_error）
    response = await _invoke_validated(chain, transformed_messages)

    update: dict = {"messages": [response]}
    if state.get("ptl_retry"):
        update["ptl_retry"] = False  # 压缩重试成功，恢复下一次 PTL 的压缩机会
    return update


# astream_events 中浮现该名的 on_custom_event 即一次丢弃重试；bridge 据此 yield message.retry。
LUMI_MODEL_RETRY_EVENT = "lumi_model_retry"


async def on_call_model_error(state: LumiAgentState, error: NodeError) -> Command:
    """CallModel 的节点级 error_handler：撞 prompt-too-long 就绕回 Summarizer 强制压缩。

    压缩后经正常拓扑重试（摘要调用在 Summarizer 节点名下运行，bridge 的压缩事件
    过滤天然生效）。``ptl_retry`` 已置位说明刚压缩过仍超长（或压缩被放行跳过），
    原样抛出——每次 PTL 只换一次压缩机会，用户看到的恒是 PTL 而非内部错误。
    非 PTL 的错误一律原样抛出，与没有 handler 时逐字节相同。

    放在 error_handler 而非节点内 try/except：LangGraph 对**节点自身返回**的
    ``Command(goto=)`` 与其条件边取并集（曾为此在 ``is_use_tool`` 里挂 ptl_retry
    守卫，免得空步把 OnAgentStop 拉进同一 superstep 分发 Stop hooks），而
    error_handler 的路由不触发条件边求值，那道守卫随之不再需要。
    """
    if not is_ptl_error(error.error) or state.get("ptl_retry"):
        raise error.error
    logger.warning("[CallModel] prompt-too-long，路由回 Summarizer 强制压缩重试")
    return Command(goto="Summarizer", update={"ptl_retry": True})


async def _invoke_validated(chain, messages: list) -> AIMessage:
    """调模型并校验 tool_calls 协议字段：畸形响应不落库，重试一次后兜底剔除。

    流式聚合偶发吐出缺 id / name 的空壳 tool_call（客户现场：qwen 一轮输出畸变），
    落库后配对 ToolMessage 与回传模型 API 都会炸，且会永久污染 checkpoint。此时响应
    尚未进 state，重试是零污染的；先发 retry 事件让前端清掉本轮已流出的文本。重试
    仍畸形则剔掉空壳照常返回（最坏是这轮没调工具），并把原始 tool_calls 打进日志定性。
    """
    response = await chain.ainvoke({"messages": messages})
    if not any(map(is_malformed_tool_call, response.tool_calls)):
        return response
    logger.warning(
        "[CallModel] 模型输出畸形 tool_calls，丢弃重试: %r", response.tool_calls
    )
    await adispatch_custom_event(LUMI_MODEL_RETRY_EVENT, {})
    response = await chain.ainvoke({"messages": messages})
    kept = [tc for tc in response.tool_calls if not is_malformed_tool_call(tc)]
    if len(kept) != len(response.tool_calls):
        logger.error("[CallModel] 重试仍畸形，剔除空壳兜底: %r", response.tool_calls)
        response.tool_calls = kept
    return response


def _cmd_messages(cmd: Command) -> list:
    """从 hook 返回的 Command 取出注入的 messages（无则空列表）。"""
    return list((cmd.update or {}).get("messages") or [])


def _pending_tool_calls(messages: list) -> list[dict]:
    """末条 AIMessage 中尚无 ToolMessage 应答的 tool_calls（审批部分拒绝时被拒的已补应答）。"""
    idx = next(
        i
        for i in range(len(messages) - 1, -1, -1)
        if isinstance(messages[i], AIMessage)
    )
    return dangling_tool_calls(messages[idx:])


async def _pre_tool_hooks(
    state: LumiAgentState,
    config: RunnableConfig,
    tools: list,
    pending: list[dict],
    visible: list[dict],
) -> tuple[Command | None, list]:
    """PreToolUse hooks（collect 模式）→ ``(需直接返回的 Command, 收集到的 reminder)``。

    ``Block`` 为 ``pending`` 里每个调用补 ToolMessage(status=error) 配对后 END（残留
    tool_call 会让 LangGraph 校验失败）；hook 自定义路由原样透传；``AdditionalContext``
    收集为 reminder，工具仍执行。
    """
    ctx = HookContext(
        state=state,
        config=config,
        event="PreToolUse",
        payload={
            "tool_calls": visible,
            "tool_names": [t.name for t in tools if not is_internal_tool(t.name)],
        },
    )
    cmd = await dispatch_hooks(
        "PreToolUse", ctx, default_goto="ToolExecutor", mode="collect"
    )
    if cmd is None:
        return None, []
    if cmd.goto == "ToolExecutor":
        return None, _cmd_messages(cmd)
    if cmd.goto != END:
        return cmd, []
    existing = _cmd_messages(cmd)
    reason = next((m.content for m in existing if isinstance(m, AIMessage)), "blocked")
    tool_msgs = [
        ToolMessage(
            content=reason,
            tool_call_id=tc.get("id", ""),
            name=tc["name"],
            status="error",
        )
        for tc in pending
    ]
    return Command(
        goto=END, update={**(cmd.update or {}), "messages": [*tool_msgs, *existing]}
    ), []


async def _post_tool_hooks(
    state: LumiAgentState,
    config: RunnableConfig,
    visible: list[dict],
    tool_messages: list[ToolMessage],
) -> Command | None:
    """PostToolUse hooks（collect 模式）：hook 看到截断后的最终 ToolMessage。"""
    ctx = HookContext(
        state=state,
        config=config,
        event="PostToolUse",
        payload={
            "tool_calls": visible,
            "tool_messages": [m for m in tool_messages if not is_internal_tool(m.name)],
        },
    )
    return await dispatch_hooks(
        "PostToolUse", ctx, default_goto="CallModel", mode="collect"
    )


def _split_tool_output(output) -> tuple[list[ToolMessage], list[Command]]:
    """ToolNode 输出归一为 ``(ToolMessage 列表, Command 列表)``。

    喂 ToolCall 列表时 ToolNode 返回 ``{"messages": [...]}``；任一工具返回 Command
    时改返 ``[Command | {"messages": [ToolMessage]}]``（见
    ``ToolNode._combine_tool_outputs``）。
    """
    items = output if isinstance(output, list) else [output]
    commands = [it for it in items if isinstance(it, Command)]
    messages = [m for it in items if isinstance(it, dict) for m in it["messages"]]
    return messages, commands


async def tool_executor(
    state: LumiAgentState,
    runtime: Runtime[LumiAgentContext],
    config: RunnableConfig,
) -> dict | Command | list:
    """工具执行器，负责执行LLM调用的工具。

    工具执行前后分发 PreToolUse / PostToolUse hooks（collect 模式）：
    - PreToolUse：``Block`` 补齐 ToolMessage(status=error) 配对后终止；
      ``AdditionalContext`` 收集为 reminder，工具仍执行，结果注入 ToolMessage 之后。
    - PostToolUse：hook 看到截断后的最终 ToolMessage，reminder 追加到末尾。
    工具自身返回 Command（ask/agent/structured_output 等控制流）的路径直返
    ``[*Command, {"messages": [...]}]``，不接 PostToolUse——这些工具用 Command 自定义
    路由，注入会破坏其控制流。
    """
    tools = list(runtime.context.tools)
    output_schema = state.get("output_schema")
    if output_schema:
        # 结构化输出真工具进 ToolExecutor 执行（与 call_model 注入同一 lru_cache 实例）
        tools = tools + [create_structured_output_tool(output_schema)]

    # 只针对尚未应答的调用（审批部分拒绝时被拒的已补应答）
    pending = _pending_tool_calls(state["messages"])
    # 内部伪工具 __structured_output__ 不暴露给用户 hook（否则宽 matcher 会误触发，
    # Block 还会破坏结构化输出流）；但保留在 pending 用于 Block 的 ToolMessage 配对。
    visible = [tc for tc in pending if not is_internal_tool(tc.get("name", ""))]
    extra_msgs: list = []
    # 无 hook 时跳过整段——避免每个工具轮白白构造 HookContext + tool_names。
    if has_hooks("PreToolUse"):
        cmd, extra_msgs = await _pre_tool_hooks(state, config, tools, pending, visible)
        if cmd is not None:
            return cmd

    tool_node = ToolNode(tools, handle_tool_errors=handle_tool_error)
    # 直接喂 ToolCall 列表：只跑未应答的；工具注入的 state 由 ToolNode 从 config 读真实图状态
    output = await tool_node.ainvoke(
        [{**tc, "type": "tool_call"} for tc in pending], config
    )
    tool_messages, commands = _split_tool_output(output)
    await truncate_tool_results(tool_messages)
    final_msgs = [*tool_messages, *extra_msgs]
    if commands:
        return [*commands, {"messages": final_msgs}]

    if has_hooks("PostToolUse"):
        post_cmd = await _post_tool_hooks(state, config, visible, tool_messages)
        if post_cmd is not None:
            final_msgs = [*final_msgs, *_cmd_messages(post_cmd)]
            if post_cmd.goto == END:
                return Command(goto=END, update={"messages": final_msgs})

    # structured_output 连续失败兜底：本轮累计失败 >= 上限时强制结束循环。
    # 计数用纯净 tool_messages（不含注入的 reminder HumanMessage，否则尾扫会被
    # HumanMessage 提前 break 导致计数失真）。
    if output_schema:
        abort_msg = _structured_output_abort_message(state, tool_messages)
        if abort_msg is not None:
            return Command(goto=END, update={"messages": [*final_msgs, abort_msg]})

    return {"messages": final_msgs}


def _structured_output_abort_message(
    state: LumiAgentState, tool_messages: list
) -> AIMessage | None:
    """本轮 structured_output 连续失败达上限时返回 abort AIMessage，否则 None。

    abort 时末尾追加人话提示而非工具内部错误，且以 assistant 收尾，方便下一轮续聊。
    """
    history = list(state.get("messages") or []) + list(tool_messages)
    fails = count_consecutive_structured_output_failures(history)
    if fails < MAX_CONSECUTIVE_FAILURES:
        return None
    logger.warning(
        "[tool_executor] structured_output 连续失败 %d 次（>=%d），强制结束循环",
        fails,
        MAX_CONSECUTIVE_FAILURES,
    )
    return AIMessage(content=format_structured_output_abort_message(fails))


def after_tool_executor(state: LumiAgentState) -> str:
    """ToolExecutor 后的条件路由：工具被取消时走向 END，否则继续 CallModel"""
    if state.get("tool_cancelled"):
        return "END"
    return "CallModel"


async def on_agent_stop(
    state: LumiAgentState,
    runtime: Runtime[LumiAgentContext],
    config: RunnableConfig,
) -> Command:
    """模型未调任何工具想结束循环时的统一入口，分发 Stop hooks。

    first_intercept 语义：第一个返非 None 的 Stop hook 拦截（如结构化输出未完成
    时注入 reminder 拉回 CallModel）；全部放行则 Command(goto=END) 正常终止。

    ``runtime`` 透传进 HookContext，供 auto_dream_stop_hook 取 context（system_prompt /
    permission_engine / memory_enabled）——它是 dream hook 拿运行时上下文的唯一通道。
    """
    ctx = HookContext(
        state=state, config=config, event="Stop", payload={}, runtime=runtime
    )
    cmd = await dispatch_hooks("Stop", ctx, default_goto="CallModel")
    return cmd if cmd is not None else Command(goto=END)


def is_use_tool(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> str:
    """条件路由函数 - 判断下一步执行哪个节点

    路由优先级：
    1. 无 tool_calls → OnAgentStop（分发 Stop hooks）
    2. 纯内部伪工具（如结构化输出）→ ToolExecutor（闭包内校验，绕过权限审批）；
       内部工具与其他工具混合的批次不绕过，落到下方正常权限评估
    3. 全部 bypass 类工具 → ToolExecutor
    5. bypass-immune 检查（所有模式）→ 命中则 HumanApproval
    6. 权限引擎 DENY（所有模式）→ HumanApproval（节点内自动拒绝，路由回 CallModel）
    7. accept_edits 模式 → 文件编辑工具(write/edit)工作区内自动放行，其余 HumanApproval
    8. privileged 模式 → ASK 命中则 HumanApproval，其余 ToolExecutor
    9. default 模式：全部 ALLOW + 边界 OK → ToolExecutor（快速路径）
    10. 其他 → HumanApproval
    """
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls:
        # 模型未调工具想结束 → OnAgentStop 节点分发 Stop hooks（默认 END）
        return "OnAgentStop"

    decision = route_decision(
        tool_calls,
        runtime.context.tool_mode,
        runtime.context.permission_engine,
    )
    # privileged 的「自动放行」本身即授权，这条路上既不审批也不过分类器，没有别的挂钩点
    if decision == "ToolExecutor" and runtime.context.tool_mode == "privileged":
        _widen_boundary_for(tool_calls, runtime)
    return decision


def _widen_boundary_for(tool_calls: list, runtime: Runtime[LumiAgentContext]) -> None:
    """授权通过后把本批越界路径所在目录纳入本会话工作区。

    边界与审批是两道正交的门，只过审批不放宽边界会同时踩两个坑——详见
    docs/architecture/permissions.md「边界与审批是两道正交的门」。回调由 bridge
    注入，headless 无 bridge 保持 None（不放宽）。

    只放宽本批里会被边界拦下的**写**调用（本机路径工具 ∩ 写操作）：批次是混合的
    （纯只读批次在 route_decision 更早处就短路了），批准一次越界 read 不该换来该
    目录的写权。
    """
    engine = runtime.context.permission_engine
    widen = runtime.context.widen_boundary
    if engine is None or widen is None:
        return
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {})
        if is_local_path_tool(name) and is_write_tool(name, args):
            widen(engine.get_boundary_violations(name, args))


async def human_approval(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> Command:
    """经在途审批 Broker 原地挂起，等待用户审批

    Graph 侧处理：
    - DENY 命中 → 跳过审批，直接拒绝并路由回 CallModel
    - 非 DENY → await broker.request 等待用户审批：
      - approve → ToolExecutor
      - reject  → END（附带拒绝原因 ToolMessage）
      - cancel  → END（附带取消原因 ToolMessage）

    权限评估、选项构建、规则持久化由 Bridge 层负责（on_custom_event 分支富化）。
    decision 为 dict: {"decision": "approve"/"reject"/"cancel", "message": "...",
    "set_tool_mode": "..."}（stop / 切会话取消挂起轮时 await 抛 CancelledError 向上冒泡）。
    """
    last_message = state["messages"][-1]
    tool_calls_data = [
        {"id": tc.get("id", ""), "name": tc["name"], "args": tc["args"]}
        for tc in last_message.tool_calls
    ]

    # DENY 命中：不发审批，直接拒绝并路由回 CallModel 让模型调整。route_decision 只
    # 返回节点名、条件边又写不了 state，DENY 与 ASK 到此同名，故此处再评估一次分辨。
    engine = runtime.context.permission_engine
    if engine is not None and any(
        engine.evaluate(tc["name"], tc.get("args", {})) == PermissionDecision.DENY
        for tc in last_message.tool_calls
    ):
        messages = build_reject_messages(
            last_message.tool_calls,
            content="你执行的此操作命中了用户的禁止策略，你的操作可能被用户视为危险操作，你应该思考此操作的风险使用更低风险的操作来完成目标。",
        )
        return Command(goto="CallModel", update={"messages": messages})

    # 无审批通道（headless：cron / workflow / 后台子代理，context.approval_broker 为 None）：
    # 无法发起交互审批，fail-closed 自动拒绝并路由回 CallModel，让自治 agent 改用无需审批的方式
    broker = runtime.context.approval_broker
    if broker is None:
        messages = build_reject_messages(
            last_message.tool_calls,
            content="当前运行环境无交互式审批通道，已自动拒绝该操作，请改用无需审批的方式完成目标。",
        )
        return Command(goto="CallModel", update={"messages": messages})

    # reject_value：本审批被 stop / 切会话收尾时返回的拒绝决策，使本轮以拒绝干净完成、
    # 保留历史（而非取消丢弃），等价于用户点了"拒绝"。
    result = await broker.request(
        {"type": "tool_approval", "tool_calls": tool_calls_data},
        {"decision": "reject", "message": "用户停止了本轮，已拒绝该操作"},
    )

    # 应答来自 wire（resume 的 value 由客户端给），形状不可信：非 dict 一律按拒绝收尾，
    # 与下方「缺项按拒绝」同一条 fail-closed 约定——否则畸形负载会以 AttributeError
    # 打断整条流式，而不是干净地拒掉这批工具调用。
    if not isinstance(result, dict):
        logger.warning(
            "[HumanApproval] 应答格式非法（%s），按拒绝处理", type(result).__name__
        )
        result = {"decision": "reject", "message": "审批应答格式非法，已拒绝该操作"}

    # 批量 {decision} 应答展开成同值列表，与逐个审批的 decisions 走同一裁决
    tool_calls = last_message.tool_calls
    decisions = result.get("decisions") or [result.get("decision", "reject")] * len(
        tool_calls
    )
    return _apply_decisions(
        tool_calls,
        decisions,
        result.get("message", ""),
        result.get("set_tool_mode"),
        runtime,
    )


def _apply_decisions(
    tool_calls: list[dict],
    decisions: list[str],
    message: str,
    set_tool_mode: str | None,
    runtime: Runtime[LumiAgentContext],
) -> Command:
    """按与 tool_calls 同序的 decisions 裁决，缺项按拒绝（fail-closed）。

    全拒绝 → END（附拒绝 / 中断原因）；有允许 → 被拒的补拒绝 ToolMessage 后进 ToolExecutor
    （只执行未应答的），执行完照常回 CallModel，让模型看到结果与被拒说明继续干活。
    """
    approved: list[dict] = []
    rejected: list[dict] = []
    for i, tc in enumerate(tool_calls):
        ok = i < len(decisions) and decisions[i] == "approve"
        (approved if ok else rejected).append(tc)
    content = message or (
        "用户中断了工具调用请求" if "cancel" in decisions else "用户拒绝了工具执行"
    )
    if not approved:
        return Command(
            goto=END,
            update={"messages": build_reject_messages(tool_calls, content=content)},
        )
    # tool_mode 是 context（运行时共享）属性，直接改即对后续工具生效——
    # 无需经 Command.update 写 state（state 已无此字段）。
    if set_tool_mode:
        runtime.context.tool_mode = set_tool_mode
    _widen_boundary_for(approved, runtime)
    update = (
        {"messages": build_reject_messages(rejected, content=content)}
        if rejected
        else None
    )
    return Command(goto="ToolExecutor", update=update)


def build_reject_messages(
    tool_calls: list[dict], content: str = "用户拒绝了工具执行"
) -> list[ToolMessage]:
    """为被拒绝/中断的工具调用构造模拟 ToolMessage 列表。

    Args:
        tool_calls: AIMessage 中的 tool_calls 列表
        content: 拒绝/中断原因文本

    Returns:
        每个 tool_call 对应一条结果的 ToolMessage
    """
    return [
        ToolMessage(
            content=content,
            tool_call_id=tc.get("id", ""),
            name=tc["name"],
        )
        for tc in tool_calls
    ]


class _ClassifierVerdict(BaseModel):
    """auto 模式分类器的裁决结果。"""

    decision: Literal["approve", "reject"] = Field(
        description="approve=自动放行；reject=自动拒绝"
    )
    reason: str = Field(description="一句话说明裁决依据，简明")


_CLASSIFIER_SYSTEM = """你是 Lumi 的工具调用安全分类器（auto 审批模式）。
基于安全性判断即将执行的一批工具调用，输出二选一裁决：
- approve：安全、符合用户当前意图的操作，自动放行
- reject：危险、破坏性、越权或与用户意图相悖的操作，自动拒绝

判断重心放在会**修改真实环境**的操作上——写入/编辑/删除文件、有副作用或改动系统状态的命令、网络提交等；这类须核对是否符合用户当前意图且无破坏性，安全则 approve，危险或越权则 reject。只读、查询、无副作用的操作直接 approve。
警惕**换工具绕过限制**：若某工具已被禁用/拦截，用 bash 的 `sed -i`、`cat >`、`tee`、重定向、`python -c`、heredoc 等去完成本该由被禁工具做的同一件事（如写/改一个 write/edit 被拦的文件），即属绕过，reject 并在 reason 点明。
注意：bash 后台运行应使用 run_in_background 参数，而非在命令里加 `&`；遇到用 `&` 后台化的命令，reject 并在 reason 提示改用参数。
只依据安全性，不替用户做产品决策。reason 用一句话说明。"""


def _latest_user_intent(messages: list) -> str:
    """取最近一条**真实**（应显示的）HumanMessage 文本，作为分类器判断意图的上下文。

    合成消息（工具回灌 / 通知 / carrier，items 声明为空）跳过；纯附件消息是
    真实用户输入，**停在这里**返回空串（本轮无文本意图，分类器保守裁决）——
    不上溯到更早轮次，否则会把上一轮的陈旧意图当本轮意图误导安全裁决。
    """
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and should_show_human_message(msg):
            return visible_user_text(msg)
    return ""


async def auto_classify(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> Command:
    """auto 模式：用 AI 分类器替代人工审批裁决一批工具调用。

    仅在 route_decision 判定「本该问人」时进入（DENY / bypass-immune 已在更早
    的免疫闸短路到 HumanApproval，不会到这里）。裁决：
    - approve → ToolExecutor（自动放行）
    - reject  → CallModel（自动拒绝，附原因让模型改用更低风险的方式，复用 DENY 语义）
    分类器调用失败 fail-closed → HumanApproval。
    """
    last_message = state["messages"][-1]
    tool_calls = last_message.tool_calls
    rendered = "\n".join(f"- {tc['name']}({tc.get('args', {})})" for tc in tool_calls)

    # chain 构造一并纳入 try：create_llm/with_structured_output 在构造期也可能抛
    # （如解析到的分类器模型缺 api_key），fail-closed 须覆盖构造与调用全程。
    try:
        # 分类器模型独立可配（lumi.json providers 分区的 classifier 指针）；未配则回退会话模型。
        clf = resolve_pointer("classifier")
        chain = structured_output(
            template=(
                "用户最近的请求：\n{user_intent}\n\n"
                "待判定的工具调用：\n{tool_calls}\n\n"
                "请基于安全性输出裁决。"
            ),
            structure=_ClassifierVerdict,
            system_prompt=_CLASSIFIER_SYSTEM,
            model_name=clf.model,
            **clf.conn_kwargs(),
        )
        verdict: _ClassifierVerdict = await chain.ainvoke(
            {
                "user_intent": _latest_user_intent(state["messages"]),
                "tool_calls": rendered,
            }
        )
    except Exception as e:
        logger.error(
            "[AutoClassify] 分类器调用失败，fail-closed 转人工审批: %s",
            e,
            exc_info=True,
        )
        return Command(goto="HumanApproval")

    logger.info("[AutoClassify] 裁决=%s 原因=%s", verdict.decision, verdict.reason)
    match verdict.decision:
        case "approve":
            _widen_boundary_for(tool_calls, runtime)
            return Command(goto="ToolExecutor")
        case _:  # reject（及任何非 approve 值）：自动拒绝，附原因回喂模型
            messages = build_reject_messages(
                tool_calls,
                content=(
                    f"此操作被 auto 模式安全分类器自动拒绝：{verdict.reason}。"
                    "你可以改用自然完成同一目标的其他工具，但**不得**用换工具的方式"
                    "绕过这条拦截（如借 bash 重定向/sed/python 去做被拦的写操作）。"
                    "若该能力确有必要，请停下并向用户说明你要做什么、为何需要授权。"
                ),
            )
            return Command(goto="CallModel", update={"messages": messages})


async def _summarize(
    messages: list,
    to_summarize: list,
    keep_tail: list,
    runtime: Runtime[LumiAgentContext],
    thread_id: str,
    token_config,
) -> dict | None:
    """摘要正常路径与 PTL 强制压缩的共用核：``compact_messages`` + 熔断记账。

    成功清零熔断计数、返回压缩写回 update；失败记一次失败、返回 ``None``——
    调用方按各自语义决定抛出（正常路径）还是放行（PTL 路径）。
    """
    try:
        update, ptl_retries = await compact_messages(
            messages, to_summarize, keep_tail, runtime.context
        )
    except Exception as exc:
        fail_count = record_circuit_failure(
            thread_id, token_config.summary_circuit_reset_seconds
        )
        logger.warning(
            "[Summarizer] 摘要生成失败 thread=%s err=%s 连续失败=%d",
            thread_id,
            type(exc).__name__,
            fail_count,
        )
        return None
    reset_circuit(thread_id)
    logger.info(
        "[Summarizer] 压缩完成，%d 条进摘要、保留 %d 条，PTL 重试 %d 次",
        len(to_summarize),
        len(keep_tail),
        ptl_retries,
    )
    return update


async def summarizer(
    state: LumiAgentState,
    runtime: Runtime[LumiAgentContext],
    config: RunnableConfig,
) -> dict:
    """串行压缩历史消息，本轮 CallModel 直接看到压缩后的 messages。

    串行拓扑：``Summarizer → PreprocessMessages → CallModel``——超阈值时当轮就地
    压缩（删历史 + 摘要作独立 carrier 消息插在重挂的用户消息之前），即将溢出的这次
    调用立刻受益。压缩先于 PreprocessMessages 的 UserPromptSubmit hook：上下文注入
    永远发生在压缩后的世界里（marker 由 ``build_compacted_update`` 恒剥，hook 扫不到
    即注入全量），在线/离线压缩后的形态同构：``[Human(<summary>), Human(ctx全量+用户消息)]``。

    缓存安全的分叉：复用主对话的 system_prompt + tools 前缀，只在末尾追加摘要指令。

    - 不超阈值（``模型窗口 * summary_threshold``，真实 usage）→ 直接放行
    - 熔断器打开（同 thread 连续失败超阈值且未到 reset）→ 直接放行
    - ``ptl_retry`` 置位（CallModel 撞 PTL 路由回来）→ 绕过阈值门走
      :func:`_ptl_forced_compact` 强制压缩
    - 触发压缩 → strip 图像后走 PTL 截头重试；失败记录熔断计数并抛出（让上层感知），
      成功则清零熔断、经 ``build_compacted_update`` 写回删除 + 摘要 + 重挂

    保留规则：尾必须是 HumanMessage（不变量，否则报错）。
    """
    token_config = get_config().config.token
    thread_id = (config.get("configurable") or {}).get("thread_id", "_anon")

    # 熔断打开：同 thread summary 连续失败超阈值，本轮直接放行 CallModel
    if is_circuit_open(
        thread_id,
        token_config.summary_failure_circuit_threshold,
        token_config.summary_circuit_reset_seconds,
    ):
        logger.warning("[Summarizer] 熔断打开 thread=%s，跳过压缩直接放行", thread_id)
        return {}

    messages = list(state["messages"])
    # PTL 反应式压缩：CallModel 撞 prompt-too-long 后路由回本节点，绕过阈值门强制压缩
    if state.get("ptl_retry"):
        return await _ptl_forced_compact(messages, runtime, thread_id, token_config)

    # 分母必须是会话实际所跑模型的窗口：静态 context_length 会把 1M 窗口的模型按 200K
    # 压——用量刚过 14% 就触发压缩。
    window = (
        resolve(runtime.context.model_name, runtime.context.provider).context_window
        or token_config.context_length
    )
    threshold = window * token_config.summary_threshold
    total_tokens = context_window_tokens(messages)
    stat = f"上下文 token {total_tokens} / 阈值 {threshold:.0f}（窗口 {window}）"
    if total_tokens < threshold:
        logger.debug(f"[Summarizer] {stat}，无需压缩")
        return {}

    logger.info(f"[Summarizer] {stat}，开始压缩")
    if not messages or not isinstance(messages[-1], HumanMessage):
        raise ValueError("[Summarizer] 最后一条消息必须是 HumanMessage")

    # 可压缩消息过少（≤1 条）时压缩收益甚微，直接放行
    if sum(1 for msg in messages[:-1] if msg.id) < 2:
        return {}

    # 摘要作独立 carrier 插在重挂的末条之前（正在被回答的真人消息由
    # build_compacted_update 自己认出来一并保住）
    update = await _summarize(
        messages, messages[:-1], [messages[-1]], runtime, thread_id, token_config
    )
    if update is None:
        raise RuntimeError("[Summarizer] 摘要生成失败")
    return update


async def _ptl_forced_compact(
    messages: list, runtime: Runtime[LumiAgentContext], thread_id: str, token_config
) -> dict:
    """CallModel 撞 PTL 后路由回 Summarizer 的强制压缩：绕过阈值门与「尾必须
    Human」不变量（PTL 多发生在工具循环中段，末条是 ToolMessage），按 API round
    保尾选材。任何原因不可压 / 摘要失败都返回 ``{}`` 放行——CallModel 重试再撞
    PTL 时 ``ptl_retry`` 已置位、直接抛原错误（用户看到的恒是 PTL 而非内部错误）。

    正在被回答的那条真人 Human 与尾部 round 一并原样重挂（``build_compacted_update``
    的 ``keep``）——PTL 多发生在工具循环中段，那条 Human 早被 round 分组卷进
    to_summarize，删掉模型就只剩摘要转述、答不准用户真正要什么。
    ``ptl_retry`` 不在此清除（CallModel 成功后才清），压缩后仍超长时守卫生效。
    """
    selected = select_for_ptl_compaction(messages)
    if selected is None:
        logger.warning("[Summarizer] PTL 强制压缩：可压缩 round 不足，放行")
        return {}
    to_summarize, tail = selected
    update = await _summarize(
        messages, to_summarize, tail, runtime, thread_id, token_config
    )
    return update if update is not None else {}


async def preprocess_messages(
    state: LumiAgentState,
    runtime: Runtime[LumiAgentContext],
    config: RunnableConfig,
) -> dict:
    """消息预处理节点：清理不完整的工具调用、重置工具取消标记，并分发
    UserPromptSubmit hooks（内置的上下文注入 hook 在此把 env / agent / skill /
    记忆索引 / LUMI.md 按 marker 比对注入末条用户消息，见 :mod:`context_inject`）。

    hook 返回的消息 update（同 id 替换末条 / 追加 reminder）合并进本节点返回值；
    goto 忽略——本节点固定边 → CallModel。历史压缩在上游 ``Summarizer`` 已完成，
    本节点恒在压缩后的世界里运行。
    """
    messages = state["messages"]
    updates: dict = {}

    # 重置工具取消标记
    if state.get("tool_cancelled"):
        updates["tool_cancelled"] = False

    result_messages = cleanup_incomplete_tool_calls(messages)

    if has_hooks("UserPromptSubmit"):
        ctx = HookContext(
            state=state, config=config, event="UserPromptSubmit", runtime=runtime
        )
        # collect：config hook 的 AdditionalContext 与内置 context_inject 的消息
        # update 合并共存。已知取舍：config hook 若返回 Command/Block 会短路、
        # 跳过其后的 context_inject（该轮不注入不写 marker，下轮按旧 marker 自愈）
        # ——视为用户显式配置的接管语义。
        cmd = await dispatch_hooks("UserPromptSubmit", ctx, mode="collect")
        if cmd is not None:
            result_messages = [*result_messages, *_cmd_messages(cmd)]

    if result_messages or updates:
        return {"messages": result_messages, **updates}
    return {"messages": []}

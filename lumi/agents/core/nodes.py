from typing import Literal

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from lumi.agents.core.hooks import HookContext, dispatch_hooks, has_hooks
from lumi.agents.core.meta_message import is_meta_message
from lumi.agents.core.node_helpers.execution import (
    handle_tool_error,
    truncate_tool_results,
)
from lumi.agents.core.node_helpers.messages import (
    cleanup_incomplete_tool_calls,
    inject_message_cache_breakpoints,
)
from lumi.agents.core.preprocessing.compact import (
    is_circuit_open,
    record_circuit_failure,
    reset_circuit,
    run_summary,
)
from lumi.agents.core.preprocessing.summary import inject_summary_into_message
from lumi.agents.core.preprocessing.turn_context import build_turn_context
from lumi.agents.core.response import message_transform
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.core.structured_tool import (
    MAX_CONSECUTIVE_FAILURES,
    STRUCTURED_OUTPUT_INSTRUCTION,
    apply_enrich_to_command,
    count_consecutive_structured_output_failures,
    create_structured_output_tool,
    format_structured_output_abort_message,
    is_internal_tool,
)
from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.permissions.routing import route_decision
from lumi.models.chain import structured_output, tool_call_chain
from lumi.models.manager import detect_protocol
from lumi.models.provider_store import resolve_classifier
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.sizing import context_window_tokens


async def call_model(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> dict:

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

    # 每轮上下文块（env/agent/skill/记忆/LUMI.md）经 tool_call_chain 作为一条 HumanMessage
    # 插在静态 system 之后（trim 之后插入 → 免截断；CC 同构，见 turn_context / _turn_context_inserter）。
    turn_context = build_turn_context(runtime)
    chain = tool_call_chain(
        actual_tools,
        system_prompt=system_prompt,
        turn_context=turn_context,
        model_name=model_name,
        max_tokens=get_config().config.agents.max_tokens,
        tool_choice=None,
        apply_effort=True,  # 思考档位只在主对话链生效
    )
    iterations = state.get("iterations", 1)

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

    response = await chain.ainvoke({"messages": transformed_messages})

    if response.tool_calls:
        logger.debug(f"[LumiAgent]正在进行第「{iterations}」次工具调用迭代")

    return {"messages": [response], "iterations": iterations + 1}


def _cmd_messages(cmd: Command) -> list:
    """从 hook 返回的 Command 取出注入的 messages（无则空列表）。"""
    return list((cmd.update or {}).get("messages") or [])


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
    工具自身返回 Command（ask/agent 等控制流）的少见路径保持直返，不接 reminder /
    PostToolUse——这些工具用 Command 自定义路由，注入会破坏其控制流。
    """
    tools = list(runtime.context.tools)
    output_schema = state.get("output_schema")
    enrich = state.get("output_enrich") if output_schema else None
    if output_schema:
        # 结构化输出真工具进 ToolExecutor 执行（与 call_model 注入同一 lru_cache 实例）
        tools = tools + [create_structured_output_tool(output_schema)]

    # 1. PreToolUse hooks
    last_message = state["messages"][-1]
    pre_tool_calls = list(getattr(last_message, "tool_calls", []) or [])
    # 内部伪工具 __structured_output__ 不暴露给用户 hook（否则宽 matcher 会误触发，
    # Block 还会破坏结构化输出流）；但保留在 pre_tool_calls 用于 Block 的 ToolMessage 配对。
    visible_tool_calls = [
        tc for tc in pre_tool_calls if not is_internal_tool(tc.get("name", ""))
    ]
    extra_msgs: list = []
    # 无 PreToolUse hook 时跳过整段——避免每个工具轮白白构造 HookContext + tool_names。
    if has_hooks("PreToolUse"):
        pre_ctx = HookContext(
            state=state,
            config=config,
            event="PreToolUse",
            payload={
                "tool_calls": visible_tool_calls,
                "tool_names": [t.name for t in tools if not is_internal_tool(t.name)],
            },
        )
        pre_cmd = await dispatch_hooks(
            "PreToolUse", pre_ctx, default_goto="ToolExecutor", mode="collect"
        )
        if pre_cmd is not None:
            if pre_cmd.goto == END:
                # Block：补齐 ToolMessage 配对，避免残留 tool_call 致 LangGraph 校验失败
                existing = _cmd_messages(pre_cmd)
                reason = next(
                    (m.content for m in existing if isinstance(m, AIMessage)), "blocked"
                )
                tool_msgs = [
                    ToolMessage(
                        content=reason,
                        tool_call_id=tc.get("id", ""),
                        name=tc["name"],
                        status="error",
                    )
                    for tc in pre_tool_calls
                ]
                return Command(
                    goto=END,
                    update={
                        **(pre_cmd.update or {}),
                        "messages": [*tool_msgs, *existing],
                    },
                )
            if pre_cmd.goto != "ToolExecutor":
                # hook 显式自定义路由，原样透传
                return pre_cmd
            extra_msgs = _cmd_messages(pre_cmd)

    # 2. 执行工具
    tool_node = ToolNode(tools, handle_tool_errors=handle_tool_error)
    tool_messages = await tool_node.ainvoke(state)

    # 3. 工具自带 Command 控制流（含 structured_output 成功写入）：保持直返
    if isinstance(tool_messages, Command):
        # structured_output 成功时 Command.update 含 structured_output，按规则 enrich
        return apply_enrich_to_command(tool_messages, enrich)
    elif isinstance(tool_messages, list):
        if any(isinstance(item, Command) for item in tool_messages):
            # 混合返回（Command 控制流 + 普通 ToolMessage）：仍要截断普通结果防 token
            # 爆炸，并对 structured_output Command 应用 enrich；PreToolUse reminder 一并
            # 追加。PostToolUse 在此罕见路径不接（含 goto 的 Command 注入会破坏控制流）。
            await truncate_tool_results(
                [m for m in tool_messages if isinstance(m, ToolMessage)]
            )
            processed = [
                apply_enrich_to_command(item, enrich)
                if isinstance(item, Command)
                else item
                for item in tool_messages
            ]
            return [*processed, *extra_msgs] if extra_msgs else processed
        messages_list = tool_messages
    else:
        messages_list = tool_messages.get("messages", [])

    # 4. 截断结果（含卸载）
    await truncate_tool_results(messages_list)

    # 5. PreToolUse 收集的 reminder 注入到 ToolMessage 之后
    final_msgs = [*messages_list, *extra_msgs]

    # 6. PostToolUse hooks（看到截断后的最终 ToolMessage）——无 hook 时跳过构造
    if has_hooks("PostToolUse"):
        post_ctx = HookContext(
            state=state,
            config=config,
            event="PostToolUse",
            payload={
                "tool_calls": visible_tool_calls,
                "tool_messages": [
                    m
                    for m in messages_list
                    if isinstance(m, ToolMessage) and not is_internal_tool(m.name)
                ],
            },
        )
        post_cmd = await dispatch_hooks(
            "PostToolUse", post_ctx, default_goto="CallModel", mode="collect"
        )
        if post_cmd is not None:
            post_extra = _cmd_messages(post_cmd)
            if post_cmd.goto == END:
                return Command(
                    goto=END, update={"messages": [*final_msgs, *post_extra]}
                )
            final_msgs = [*final_msgs, *post_extra]

    # 7. structured_output 连续失败兜底：本轮累计失败 >= 上限时强制结束循环。
    #    计数用纯净 messages_list（不含注入的 reminder HumanMessage，否则尾扫会被
    #    HumanMessage 提前 break 导致计数失真）。
    if output_schema:
        abort_msg = _structured_output_abort_message(state, messages_list)
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


def policy_reject(state: LumiAgentState) -> Command:
    """通用策略拒绝节点 — 自动拒绝被执行模式策略阻止的工具调用

    为每个被阻止的 tool_call 生成拒绝 ToolMessage，路由回 CallModel 让模型调整。
    确保 tool_call_id 匹配（避免 LangGraph 校验失败）。
    """
    from lumi.agents.permissions.mode_policy import check_policy, get_policy

    mode = state.get("execution_mode", "normal")
    policy = get_policy(mode)

    last_message = state["messages"][-1]
    messages = []
    for tc in last_message.tool_calls:
        if policy is not None:
            result = check_policy(policy, tc.get("name", ""), tc.get("args", {}))
        else:
            result = None

        if result is not None and not result.allowed:
            content = (
                f"[{policy.label}] 操作被阻止: {result.reason}。"
                f"当前处于 {policy.label}，只允许策略内的操作。"
            )
        else:
            content = f"[{policy.label}] 同批次中存在被阻止的操作，此调用被跳过。"
        messages.append(
            ToolMessage(
                content=content,
                tool_call_id=tc.get("id", ""),
                name=tc["name"],
            )
        )
    return Command(goto="CallModel", update={"messages": messages})


def is_use_tool(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> str:
    """条件路由函数 - 判断下一步执行哪个节点

    路由优先级：
    1. 无 tool_calls → OnAgentStop（分发 Stop hooks）
    2. 纯内部伪工具（如结构化输出）→ ToolExecutor（闭包内校验，绕过权限审批）；
       内部工具与其他工具混合的批次不绕过，落到下方正常权限评估
    3. 全部 bypass 类工具 → ToolExecutor
    4. 执行模式策略守卫 → PolicyReject（Layer 2 模式级工具限制）
    5. bypass-immune 检查（所有模式）→ 命中则 HumanApproval
    6. 权限引擎 DENY（所有模式）→ HumanApproval（节点内自动拒绝，路由回 CallModel）
    7. accept_edits 模式 → 文件编辑工具(write/edit)工作区内自动放行，其余 HumanApproval
    8. privileged 模式 → ASK 命中则 HumanApproval，其余 ToolExecutor
    9. default 模式：全部 ALLOW + 边界 OK → ToolExecutor（快速路径）
    10. 其他 → HumanApproval
    """
    messages = state.get("messages", [])
    if not messages:
        logger.warning("[is_use_tool] 消息列表为空，无法判断工具调用")
        return "END"

    last_message = messages[-1]
    if last_message is None:
        logger.warning("[is_use_tool] 最后一条消息为 None")
        return "END"

    tool_calls = getattr(last_message, "tool_calls", None) or []
    if not isinstance(tool_calls, list):
        logger.error(f"[is_use_tool] tool_calls 类型异常：{type(tool_calls)}")
        tool_calls = []

    if not tool_calls:
        # 模型未调工具想结束 → OnAgentStop 节点分发 Stop hooks（默认 END）
        return "OnAgentStop"

    return route_decision(
        tool_calls,
        runtime.context.tool_mode,
        state.get("execution_mode", "normal"),
        runtime.context.permission_engine,
    )


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

    # DENY 命中：跳过 interrupt，直接拒绝并路由回 CallModel 让模型调整
    # 注：is_use_tool 已将 DENY 路由到此节点，此处为防御性二次确认
    engine = runtime.context.permission_engine
    if engine is not None:
        for tc in last_message.tool_calls:
            try:
                decision = engine.evaluate(tc["name"], tc.get("args", {}))
                if decision == PermissionDecision.DENY:
                    messages = _build_reject_messages(
                        last_message.tool_calls,
                        content="你执行的此操作命中了用户的禁止策略，你的操作可能被用户视为危险操作，你应该思考此操作的风险使用更低风险的操作来完成目标。",
                    )
                    return Command(goto="CallModel", update={"messages": messages})
            except Exception as e:
                logger.error(
                    "[HumanApproval] DENY 检查异常 (%s): %s, 保守拒绝",
                    tc["name"],
                    e,
                    exc_info=True,
                )
                messages = _build_reject_messages(
                    last_message.tool_calls,
                    content="权限评估异常，无法确认操作安全性，已自动拒绝。",
                )
                return Command(goto="CallModel", update={"messages": messages})

    # 无审批通道（headless：cron / workflow / 后台子代理，context.approval_broker 为 None）：
    # 无法发起交互审批，fail-closed 自动拒绝并路由回 CallModel，让自治 agent 改用无需审批的方式
    broker = runtime.context.approval_broker
    if broker is None:
        messages = _build_reject_messages(
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

    # 解析 decision 值
    set_tool_mode: str | None = None
    if isinstance(result, dict):
        decision = result.get("decision", "reject")
        message = result.get("message", "")
        set_tool_mode = result.get("set_tool_mode")
    else:
        # 兼容字符串（简单场景 / headless）
        decision = str(result)
        message = ""

    match decision:
        case "approve":
            # tool_mode 是 context（运行时共享）属性，直接改即对后续工具生效——
            # 无需经 Command.update 写 state（state 已无此字段）。
            if set_tool_mode:
                runtime.context.tool_mode = set_tool_mode
            return Command(goto="ToolExecutor")
        case "cancel":
            messages = _build_reject_messages(
                last_message.tool_calls,
                content=message or "用户中断了工具调用请求",
            )
            return Command(goto=END, update={"messages": messages})
        case _:  # reject 及默认
            messages = _build_reject_messages(
                last_message.tool_calls,
                content=message or "用户拒绝了工具执行",
            )
            return Command(goto=END, update={"messages": messages})


def _build_reject_messages(
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
    """取最近一条**真实** HumanMessage 文本，作为分类器判断意图的上下文。

    跳过 meta/注入型 HumanMessage（system-reminder、工具回灌等），否则分类器会把
    系统注入内容误当成用户意图，污染安全裁决。
    """
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and not is_meta_message(msg):
            content = msg.content
            if isinstance(content, str):
                return content
            # 多模态 content：拼接其中的文本块
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
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
        clf = resolve_classifier()
        conn = {
            k: v for k, v in (("base_url", clf.base_url), ("api_key", clf.api_key)) if v
        }
        chain = structured_output(
            template=(
                "用户最近的请求：\n{user_intent}\n\n"
                "待判定的工具调用：\n{tool_calls}\n\n"
                "请基于安全性输出裁决。"
            ),
            structure=_ClassifierVerdict,
            system_prompt=_CLASSIFIER_SYSTEM,
            model_name=clf.model,
            **conn,
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
            return Command(goto="ToolExecutor")
        case _:  # reject（及任何非 approve 值）：自动拒绝，附原因回喂模型
            messages = _build_reject_messages(
                tool_calls,
                content=(
                    f"此操作被 auto 模式安全分类器自动拒绝：{verdict.reason}。"
                    "你可以改用自然完成同一目标的其他工具，但**不得**用换工具的方式"
                    "绕过这条拦截（如借 bash 重定向/sed/python 去做被拦的写操作）。"
                    "若该能力确有必要，请停下并向用户说明你要做什么、为何需要授权。"
                ),
            )
            return Command(goto="CallModel", update={"messages": messages})


async def summarizer(
    state: LumiAgentState,
    runtime: Runtime[LumiAgentContext],
    config: RunnableConfig,
) -> dict:
    """串行压缩历史消息，本轮 CallModel 直接看到压缩后的 messages。

    串行拓扑：``PreprocessMessages → Summarizer → CallModel``——超阈值时当轮就地
    压缩（``RemoveMessage`` 删历史 + 摘要前置到末条 Human），即将溢出的这次调用
    立刻受益，而非等下一轮。

    缓存安全的分叉：复用主对话的 system_prompt + tools 前缀，只在末尾追加摘要指令。

    - 不超阈值（``context_length * summary_threshold``，真实 usage）→ 直接放行
    - 熔断器打开（同 thread 连续失败超阈值且未到 reset）→ 直接放行
    - 触发压缩 → strip 图像后走 PTL 截头重试；失败记录熔断计数并抛出（让上层感知），
      成功则清零熔断、写回 ``RemoveMessage`` + 摘要消息

    保留规则：头 SystemMessage 不参与摘要；尾必须是 HumanMessage（不变量，否则报错）。
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

    original_messages = list(state["messages"])
    threshold = token_config.context_length * token_config.summary_threshold
    total_tokens = context_window_tokens(original_messages)
    if total_tokens < threshold:
        logger.debug(
            f"[Summarizer] 上下文 token ({total_tokens}) < 阈值 ({threshold})，无需压缩"
        )
        return {}

    logger.info(
        f"[Summarizer] 上下文 token ({total_tokens}) >= 阈值 ({threshold})，开始压缩"
    )

    # 跳过头部 SystemMessage（不参与摘要、不删除）；尾必须是 HumanMessage
    messages = original_messages
    if messages and isinstance(messages[0], SystemMessage):
        messages = messages[1:]
    if not messages or not isinstance(messages[-1], HumanMessage):
        raise ValueError("[Summarizer] 最后一条消息必须是 HumanMessage")

    messages_to_summarize = messages[:-1]
    summarized_ids = [msg.id for msg in messages_to_summarize if msg.id]
    # 可压缩消息过少（≤1 条）时压缩收益甚微，直接放行
    if len(summarized_ids) < 2:
        return {}

    prompt = get_config().load_prompt("SUMMARY")
    if not prompt:
        raise ValueError(
            "未找到摘要提示词配置 'SUMMARY.md'。\n"
            "请在 .lumi/prompts/SUMMARY.md 中配置摘要提示词。"
        )

    try:
        summary_text, ptl_retries = await run_summary(
            messages_to_summarize,
            prompt,
            tools=runtime.context.tools,
            system_prompt=runtime.context.system_prompt,
            model_name=runtime.context.model_name,
            max_retry=token_config.summary_ptl_retry_max,
            drop_ratio=token_config.summary_ptl_retry_drop_ratio,
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
        raise
    reset_circuit(thread_id)
    logger.info(
        f"[Summarizer] 压缩完成，{len(summarized_ids)} 条消息，PTL 重试 {ptl_retries} 次"
    )

    # 摘要前置到末条 Human。env/skill/agent/记忆 等 reminder 不在此重注入——它们由下游
    # CallModel 每轮 prepend 的瞬态上下文消息承载（见 turn_context），压缩后的下一次
    # CallModel 自带最新上下文，无需在持久历史里重建。
    last_human = original_messages[-1]
    new_last_human = inject_summary_into_message(last_human, summary_text)

    return {
        "messages": [RemoveMessage(id=mid) for mid in summarized_ids]
        + [RemoveMessage(id=last_human.id), new_last_human]
    }


async def preprocess_messages(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> dict:
    """消息预处理节点：清理不完整的工具调用、重置工具取消标记。

    env / agent / skill / 记忆 / LUMI.md 等 reminder 已改由 ``call_model`` 每轮 prepend
    一条瞬态上下文消息承载（见 :mod:`turn_context`），此处不再注入。历史压缩在下游
    ``Summarizer`` 节点完成。
    """
    messages = state["messages"]
    updates: dict = {}

    # 重置工具取消标记
    if state.get("tool_cancelled"):
        updates["tool_cancelled"] = False

    result_messages = cleanup_incomplete_tool_calls(messages)

    if result_messages or updates:
        return {"messages": result_messages, **updates}
    return {"messages": []}

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
from langgraph.types import Command, interrupt

from lumi.agents.core.hooks import HookContext, dispatch_hooks, has_hooks
from lumi.agents.core.node_helpers.execution import (
    handle_tool_error,
    truncate_tool_results,
)
from lumi.agents.core.node_helpers.messages import (
    cleanup_incomplete_tool_calls,
    inject_message_cache_breakpoints,
)
from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector
from lumi.agents.core.preprocessing.skills import inject_skills_into_message
from lumi.agents.core.preprocessing.summary import inject_summary_into_message
from lumi.agents.core.preprocessing.system_info import inject_system_info_into_message
from lumi.agents.core.response import extract_ainvoke_content, message_transform
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
from lumi.models.chain import tool_call_chain
from lumi.models.manager import detect_protocol
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import tiktoken_counter


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

    chain = tool_call_chain(
        actual_tools,
        system_prompt=system_prompt,
        model_name=model_name,
        max_tokens=get_config().config.agents.max_tokens,
        tool_choice=None,
        apply_effort=True,  # 思考档位只在主对话链生效
    )
    iterations = state.get("iterations", 1)

    # Anthropic 模型：为对话消息注入缓存断点（滑动窗口策略）
    messages = list(state["messages"])
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


async def on_agent_stop(state: LumiAgentState, config: RunnableConfig) -> Command:
    """模型未调任何工具想结束循环时的统一入口，分发 Stop hooks。

    first_intercept 语义：第一个返非 None 的 Stop hook 拦截（如结构化输出未完成
    时注入 reminder 拉回 CallModel）；全部放行则 Command(goto=END) 正常终止。
    """
    ctx = HookContext(state=state, config=config, event="Stop", payload={})
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
        state.get("tool_mode", "default"),
        state.get("execution_mode", "normal"),
        runtime.context.permission_engine,
    )


def human_approval(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> Command:
    """使用 interrupt 暂停执行，等待用户审批

    Graph 侧处理：
    - DENY 命中 → 跳过 interrupt，直接拒绝并路由回 CallModel
    - 非 DENY → interrupt 等待用户审批：
      - approve → ToolExecutor
      - reject  → END（附带拒绝原因 ToolMessage）
      - cancel  → END（附带取消原因 ToolMessage）

    权限评估、选项构建、规则持久化由 TUI/Bridge 层负责。
    resume 值为 dict: {"decision": "approve"/"reject"/"cancel", "message": "..."}
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

    result = interrupt({"type": "tool_approval", "tool_calls": tool_calls_data})

    # 解析 resume 值
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
            update: dict = {}
            if set_tool_mode:
                update["tool_mode"] = set_tool_mode
            return (
                Command(goto="ToolExecutor", update=update)
                if update
                else Command(goto="ToolExecutor")
            )
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


async def summarizer(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> dict:
    """总结历史聊天消息，记录摘要信息到 state（不直接替换）

    此函数在后台运行，与 CallModel 并行执行。
    生成的摘要会在下一轮对话时由 preprocess_messages 执行实际替换。

    缓存安全的分叉：复用主对话的 system_prompt + tools 前缀，
    只在末尾追加摘要指令，前面全部命中缓存。

    触发条件：
    - 消息 token 数 >= context_length * summary_threshold

    保留规则：
    - 头：SystemMessage 不参与摘要
    - 尾：必须是 HumanMessage，否则报错
    - 中间：生成摘要并记录 message id，供后续替换
    """
    messages = list(state["messages"])  # 复制原始消息

    # 0. 检查是否已经生成过摘要
    if state.get("summary", {}).get("summarized_ids") and state.get("summary", {}).get(
        "summary_text"
    ):
        return {"summary": {}}

    # 1. 计算 token，判断是否需要触发摘要
    token_config = get_config().config.token
    threshold = token_config.context_length * token_config.summary_threshold
    total_tokens = tiktoken_counter(messages)

    if total_tokens < threshold:
        logger.debug(
            f"[Summarizer] 消息 token ({total_tokens}) < 阈值 ({threshold})，无需摘要"
        )
        return {"summary": {}}

    logger.info(
        f"[Summarizer] 消息 token ({total_tokens}) >= 阈值 ({threshold})，开始生成摘要"
    )

    # 2. 跳过头部 SystemMessage（不参与摘要）
    if messages and isinstance(messages[0], SystemMessage):
        messages = messages[1:]

    # 3. 校验尾部（必须是 HumanMessage）
    if not messages or not isinstance(messages[-1], HumanMessage):
        raise ValueError("[Summarizer] 最后一条消息必须是 HumanMessage")

    # 保留尾部消息（不进行摘要）
    messages_to_summarize = messages[:-1]

    # 4. 记录需要总结的 message id
    summarized_ids = [msg.id for msg in messages_to_summarize]

    # 5. 生成摘要
    prompt = get_config().load_prompt("SUMMARY")
    if not prompt:
        raise ValueError(
            "未找到摘要提示词配置 'SUMMARY.md'。\n"
            "请在 .lumi/prompts/SUMMARY.md 中配置摘要提示词。"
        )

    # 缓存安全的分叉：使用与主对话相同的 system_prompt + tools 构建 chain，
    # 确保请求前缀一致，复用 Prompt Caching。
    # 传入 tools 仅为保持缓存前缀，摘要本身不需要工具调用。
    summary_messages = messages_to_summarize + [HumanMessage(content=prompt)]
    chain = tool_call_chain(
        runtime.context.tools,
        system_prompt=runtime.context.system_prompt,
        model_name=runtime.context.model_name,
        streaming=False,
    )
    response = await chain.ainvoke({"messages": summary_messages})
    summary_text = extract_ainvoke_content(response.content)

    logger.info(f"[Summarizer] 摘要生成完成，压缩 {len(summarized_ids)} 条消息")

    # 6. 返回摘要信息（dict 格式），供 preprocess_messages 执行实际替换
    return {
        "summary": {
            "summarized_ids": summarized_ids,
            "summary_text": summary_text,
        }
    }


async def preprocess_messages(state: LumiAgentState) -> dict:
    """消息预处理节点，在调用模型前执行以下操作:

    0. 检查并执行摘要替换（如果 state["summary"] 有值）
    1. 清理不完整的工具调用
    2. 技能动态注入（检测 .skills/ 变更并将技能列表注入最后一条用户消息）
    """
    messages = state["messages"]
    result_messages = []
    updates: dict = {}

    # 重置工具取消标记
    if state.get("tool_cancelled"):
        updates["tool_cancelled"] = False

    # 0. 检查并执行摘要替换
    summary_data = state.get("summary", {})
    if summary_data and summary_data.get("summarized_ids"):
        summarized_ids = summary_data["summarized_ids"]
        summary_text = summary_data["summary_text"]

        # 删除所有被摘要的消息
        for msg_id in summarized_ids:
            result_messages.append(RemoveMessage(id=msg_id))

        # 找到最后一条 HumanMessage（用户当前消息）
        last_human = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_human = msg
                break

        if last_human is not None:
            # 注入摘要到用户消息
            new_msg = inject_summary_into_message(last_human, summary_text)

            # 摘要后注入当前技能列表，避免 summary 吞掉之前的 system-reminder
            skills, _ = SkillChangeDetector.get_instance().check()
            if skills:
                new_msg = inject_skills_into_message(new_msg, skills)

            # 注入系统环境信息
            new_msg = inject_system_info_into_message(new_msg)

            result_messages.append(RemoveMessage(id=last_human.id))
            result_messages.append(new_msg)

        logger.info(f"[PreprocessMessages] 已替换 {len(summarized_ids)} 条消息为摘要")
        return {"messages": result_messages, "summary": {}, **updates}

    # 1. 清理不完整的工具调用
    result_messages.extend(cleanup_incomplete_tool_calls(messages))

    # 2. 技能动态注入 + 系统信息注入
    last_human = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human = msg
            break

    if last_human is not None:
        new_msg = last_human
        need_replace = False

        # 技能变更时注入技能列表
        detector = SkillChangeDetector.get_instance()
        skills, changed = detector.check()
        if changed and skills:
            new_msg = inject_skills_into_message(new_msg, skills)
            need_replace = True

        # 首条消息注入系统环境信息
        human_count = sum(1 for m in messages if isinstance(m, HumanMessage))
        if human_count <= 1:
            new_msg = inject_system_info_into_message(new_msg)
            need_replace = True

        if need_replace:
            result_messages.append(RemoveMessage(id=last_human.id))
            result_messages.append(new_msg)

    if result_messages or updates:
        return {"messages": result_messages, **updates}
    return {"messages": []}

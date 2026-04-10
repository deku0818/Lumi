from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from langgraph.runtime import Runtime

from lumi.agents.core.node_helpers.execution import (
    handle_tool_error,
    truncate_tool_results,
)
from lumi.agents.core.node_helpers.messages import (
    cleanup_incomplete_tool_calls,
    inject_message_cache_breakpoints,
)
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.tools.capability import is_file_edit_tool, is_write_tool
from lumi.agents.tools.permissions.models import PermissionDecision
from lumi.agents.tools.permissions.safety import is_bypass_immune
from lumi.agents.core.structured_tool import (
    STRUCTURED_OUTPUT_INSTRUCTION,
    apply_output_enrich,
    create_structured_output_tool,
    extract_structured_args,
    is_structured_output_call,
)
from lumi.agents.core.response import extract_ainvoke_content
from lumi.agents.core.preprocessing.summary import inject_summary_into_message
from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector
from lumi.agents.core.preprocessing.skills import inject_skills_into_message
from lumi.agents.core.preprocessing.system_info import inject_system_info_into_message
from lumi.utils.llm_chain import tiktoken_counter, tool_call_chain
from lumi.utils.logger import logger
from lumi.utils.model_manager import detect_model_type
from lumi.utils.read_config import get_config


async def call_model(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> dict:

    system_prompt = runtime.context.system_prompt
    model_name = runtime.context.model_name
    tools = runtime.context.tools

    # ToolStrategy: 当 output_schema 存在时注入结构化输出工具，强制 tool_choice="any"
    actual_tools = list(tools)
    tool_choice = None
    output_schema = state.get("output_schema")
    if output_schema:
        actual_tools.append(create_structured_output_tool(output_schema))
        system_prompt += STRUCTURED_OUTPUT_INSTRUCTION
        tool_choice = "any"

    chain = tool_call_chain(
        actual_tools,
        system_prompt=system_prompt,
        model_name=model_name,
        max_tokens=get_config().config.agents.max_tokens,
        tool_choice=tool_choice,
    )
    iterations = state.get("iterations", 1)

    # Anthropic 模型：为对话消息注入缓存断点（滑动窗口策略）
    messages = list(state["messages"])
    if detect_model_type(model_name) in ("anthropic", "bedrock"):
        inject_message_cache_breakpoints(messages)

    response = await chain.ainvoke({"messages": messages})

    if response.tool_calls:
        logger.debug(f"[SimpleAgent]正在进行第「{iterations}」次工具调用迭代")

    return {"messages": [response], "iterations": iterations + 1}


async def tool_executor(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> dict | Command | list:
    """工具执行器，负责执行LLM调用的工具"""
    tools = runtime.context.tools

    tool_node = ToolNode(tools, handle_tool_errors=handle_tool_error)
    tool_messages = await tool_node.ainvoke(state)

    # 3. 处理返回值
    # 兼容 ToolNode 返回值格式（可能是字典、列表或 Command）
    if isinstance(tool_messages, Command):
        return tool_messages
    elif isinstance(tool_messages, list):
        has_command = any(isinstance(item, Command) for item in tool_messages)
        if has_command:
            return tool_messages
        messages_list = tool_messages
    else:
        messages_list = tool_messages.get("messages", [])

    # 4. 截断结果（含卸载）
    await truncate_tool_results(messages_list)

    return {"messages": messages_list}


def after_tool_executor(state: LumiAgentState) -> str:
    """ToolExecutor 后的条件路由：工具被取消时走向 END，否则继续 CallModel"""
    if state.get("tool_cancelled"):
        return "END"
    return "CallModel"


def policy_reject(state: LumiAgentState) -> Command:
    """通用策略拒绝节点 — 自动拒绝被执行模式策略阻止的工具调用

    为每个被阻止的 tool_call 生成拒绝 ToolMessage，路由回 CallModel 让模型调整。
    确保 tool_call_id 匹配（避免 LangGraph 校验失败）。
    """
    from lumi.agents.tools.permissions.mode_policy import check_policy, get_policy

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
    1. 无 tool_calls → END
    2. 结构化输出 → ExtractStructuredOutput
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
        return "END"

    if is_structured_output_call(tool_calls):
        return "ExtractStructuredOutput"

    # 权限引擎 DENY 检查（优先于 bypass，deny 规则不可绕过）
    engine = runtime.context.permission_engine
    tool_mode = state.get("tool_mode", "default")

    if engine is not None:
        engine.reload()
        for tc in tool_calls:
            try:
                decision = engine.evaluate(tc["name"], tc.get("args", {}))
                if decision == PermissionDecision.DENY:
                    return "HumanApproval"
            except Exception as e:
                logger.error(
                    "[PermissionCheck] DENY 前置检查异常 (%s): %s",
                    tc["name"],
                    e,
                    exc_info=True,
                )

    # 只读工具跳过审批，直接执行
    if all(
        not is_write_tool(tc.get("name", ""), tc.get("args", {})) for tc in tool_calls
    ):
        return "ToolExecutor"

    # 执行模式策略守卫（Layer 2: 根据当前模式策略拦截不允许的工具调用）
    execution_mode = state.get("execution_mode", "normal")
    if execution_mode != "normal":
        from lumi.agents.tools.permissions.mode_policy import check_policy, get_policy

        policy = get_policy(execution_mode)
        if policy is not None:
            for tc in tool_calls:
                result = check_policy(policy, tc.get("name", ""), tc.get("args", {}))
                if not result.allowed:
                    logger.info(
                        "[PolicyGuard] %s 拒绝: %s - %s",
                        policy.label,
                        tc.get("name"),
                        result.reason,
                    )
                    return "PolicyReject"

    # bypass-immune 安全检查（所有模式都执行）
    for tc in tool_calls:
        args = tc.get("args", {})
        try:
            immune, reason = is_bypass_immune(tc["name"], args)
        except Exception as e:
            logger.error(
                "[SafetyCheck] bypass-immune 检查异常 (%s): %s, 保守要求审批",
                tc["name"],
                e,
                exc_info=True,
            )
            return "HumanApproval"
        if immune:
            logger.warning("[SafetyCheck] Bypass-immune: %s", reason)
            return "HumanApproval"

    # accept_edits 模式：文件编辑工具(write/edit)在工作区内自动放行
    if tool_mode == "accept_edits":
        all_auto = True
        for tc in tool_calls:
            name = tc.get("name", "")
            if is_file_edit_tool(name):
                if engine is not None and engine.check_workspace_boundary(
                    name, tc.get("args", {})
                ):
                    continue
                all_auto = False
                break
            else:
                all_auto = False
                break
        if all_auto:
            return "ToolExecutor"
        return "HumanApproval"

    # 权限引擎完整评估（deny 已在上方处理，此处处理 allow/ask/unmatched）

    if engine is not None:
        engine.reload()
        has_deny = False
        has_ask = False
        all_allowed = True

        for tc in tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            try:
                decision = engine.evaluate(name, args)
                boundary_ok = engine.check_workspace_boundary(name, args)
                logger.debug(
                    "[PermissionCheck] 工具 %s: decision=%s, boundary_ok=%s",
                    name,
                    decision.value,
                    boundary_ok,
                )
                if decision == PermissionDecision.DENY:
                    has_deny = True
                    break
                if decision == PermissionDecision.ASK:
                    has_ask = True
                if decision != PermissionDecision.ALLOW or not boundary_ok:
                    all_allowed = False
            except Exception as e:
                logger.error(
                    "[PermissionCheck] 工具 %s 权限评估异常: %s, 保守要求审批",
                    name,
                    e,
                    exc_info=True,
                )
                # 评估异常时保守处理：所有模式都要求人工审批
                return "HumanApproval"

        # DENY：所有模式下路由到审批节点（节点内自动拒绝）
        if has_deny:
            return "HumanApproval"

        # privileged 模式：ASK 仍需审批，其余自动放行
        if tool_mode == "privileged":
            if has_ask:
                return "HumanApproval"
            return "ToolExecutor"

        # default 模式：全部 ALLOW + 边界 OK 才直接执行
        if all_allowed:
            return "ToolExecutor"

        return "HumanApproval"

    # engine is None：privileged 放行，default/accept_edits 审批
    if tool_mode == "privileged":
        logger.warning("[is_use_tool] 权限引擎不可用，privileged 模式直接放行")
        return "ToolExecutor"
    logger.warning("[is_use_tool] 权限引擎不可用，回退到人工审批")
    return "HumanApproval"


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


async def extract_structured_output(state: LumiAgentState) -> dict:
    """从结构化输出工具调用中提取结构化数据

    从最后一条 AIMessage 的 tool_calls 中找到 __structured_output__ 结构化输出工具，
    提取其 args 作为 structured_output。
    """
    last_msg = state.get("messages", [])[-1]
    tool_calls = getattr(last_msg, "tool_calls", [])
    args = extract_structured_args(tool_calls)

    if args is not None:
        enrich_rules = state.get("output_enrich")
        if enrich_rules:
            try:
                args = apply_output_enrich(args, enrich_rules)
            except Exception:
                logger.error(
                    "[ExtractStructuredOutput] output_enrich 执行失败，"
                    "返回未注入的原始结构化输出",
                    exc_info=True,
                )
        logger.debug(
            "[ExtractStructuredOutput] 成功从结构化输出工具调用中提取结构化数据"
        )
        return {"structured_output": args}

    logger.warning("[ExtractStructuredOutput] 未找到结构化输出工具调用，返回空结果")
    return {"structured_output": {}}


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
        temperature=1,
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

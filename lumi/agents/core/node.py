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

from lumi.agents.core.executor_tools import (
    handle_tool_error,
    truncate_tool_results,
)
from lumi.agents.core.message_tools import (
    cleanup_incomplete_tool_calls,
    inject_message_cache_breakpoints,
)
from lumi.agents.core.scheme import LumiAgentContext, LumiAgentState
from lumi.agents.tools.permissions.matcher import (
    build_exact_expr,
    build_pattern_expr,
)
from lumi.agents.tools.permissions.models import BYPASS_TOOLS, PermissionDecision
from lumi.agents.tools.workspace import add_authorized_directory
from lumi.agents.core.structured_tool import (
    STRUCTURED_OUTPUT_INSTRUCTION,
    apply_output_enrich,
    create_structured_output_tool,
    extract_structured_args,
    is_structured_output_call,
)
from lumi.agents.base.response_service import extract_ainvoke_content
from lumi.agents.core.summary_injector import inject_summary_into_message
from lumi.agents.tools.skill_detector import SkillChangeDetector
from lumi.agents.tools.skill_injector import inject_skills_into_message
from lumi.agents.tools.system_info_injector import inject_system_info_into_message
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


def is_use_tool(state: LumiAgentState, runtime: Runtime[LumiAgentContext]) -> str:
    """条件路由函数 - 判断下一步执行哪个节点

    路由优先级：
    - 有 tool_calls 且包含结构化输出工具 → "ExtractStructuredOutput"
    - BYPASS_TOOLS (如 ask) → "ToolExecutor" 直接执行
    - privileged 模式（tool_mode） → "ToolExecutor" 直接执行
    - auto 模式 + 全部 allow + 边界检查通过 → "ToolExecutor" 直接执行
    - auto 模式 + 任意 deny/unmatched → "HumanApproval" 等待审批
    - engine is None 时回退到简单审批流程
    - 无 tool_calls → "END" 结束流程
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

    # BYPASS_TOOLS 始终直接执行
    if all(tc.get("name") in BYPASS_TOOLS for tc in tool_calls):
        return "ToolExecutor"

    tool_mode = state.get("tool_mode", "auto")

    # 特权模式：跳过所有审批（bypass-immune 除外）
    if tool_mode == "privileged":
        from lumi.agents.tools.permissions.safety import is_bypass_immune

        for tc in tool_calls:
            try:
                immune, reason = is_bypass_immune(tc["name"], tc.get("args", {}))
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
        return "ToolExecutor"

    # 使用权限引擎评估
    engine = runtime.context.permission_engine
    if engine is not None:
        engine.reload()

        if tool_mode == "auto":
            # auto 模式：全部 allow 且未越界才直接执行，否则需要审批
            all_allowed = True
            for tc in tool_calls:
                try:
                    decision = engine.evaluate(tc["name"], tc.get("args", {}))
                    boundary_ok = engine.check_workspace_boundary(
                        tc["name"], tc.get("args", {})
                    )
                    logger.debug(
                        "[PermissionCheck] 工具 %s: decision=%s, boundary_ok=%s",
                        tc["name"],
                        decision.value,
                        boundary_ok,
                    )
                    if decision != PermissionDecision.ALLOW or not boundary_ok:
                        all_allowed = False
                        break
                except Exception as e:
                    # 权限评估异常时保守处理：要求人工审批，并记录详细错误
                    all_allowed = False
                    logger.error(
                        f"[PermissionCheck] 工具 {tc['name']} 权限评估异常：{e}",
                        exc_info=True,
                    )
                    break

            if all_allowed:
                return "ToolExecutor"
            return "HumanApproval"

    # engine is None 时保守处理：需要审批
    logger.warning("[is_use_tool] 权限引擎不可用，tool_mode=auto 回退到人工审批")
    return "HumanApproval"


def human_approval(
    state: LumiAgentState, runtime: Runtime[LumiAgentContext]
) -> Command:
    """使用 interrupt 暂停执行，等待用户审批

    根据 tool_mode 和 PermissionDecision 构造不同的中断数据：
    - auto + deny/unmatched → 权限审批（权限选项 + deny 警告）
    - privileged → 不应到达此节点（已在 is_use_tool 直接执行）
    - engine is None → 回退到简单审批流程
    """
    last_message = state["messages"][-1]
    engine = runtime.context.permission_engine

    tool_calls_data = [
        {"name": tc["name"], "args": tc["args"]} for tc in last_message.tool_calls
    ]

    # 无权限引擎时回退到简单审批
    if engine is None:
        decision = interrupt(
            {
                "type": "tool_approval",
                "message": "是否执行以下工具？",
                "tool_calls": tool_calls_data,
            }
        )
        if decision == "approve":
            return Command(goto="ToolExecutor")
        reject_messages = _build_reject_messages(last_message.tool_calls)
        return Command(goto=END, update={"messages": reject_messages})

    # 收集权限决策、边界违规和警告
    decisions: list[PermissionDecision] = []
    warnings: list[str] = []
    boundary_violations: list[str] = []

    from lumi.agents.tools.permissions.validators import validate_bash_command

    for tc in last_message.tool_calls:
        name, args = tc["name"], tc.get("args", {})

        # 工作区边界检查
        try:
            violations = engine.get_boundary_violations(name, args)
            boundary_violations.extend(violations)
        except Exception as e:
            logger.error(
                "[HumanApproval] 边界检查异常 (%s): %s", name, e, exc_info=True
            )

        # 权限评估
        try:
            decision = engine.evaluate(name, args)
        except Exception as e:
            logger.error(
                "[HumanApproval] 权限评估异常 (%s): %s", name, e, exc_info=True
            )
            decision = PermissionDecision.UNMATCHED
        decisions.append(decision)
        if decision == PermissionDecision.DENY:
            warnings.append(f"⚠ 工具 {name} 命中 deny 规则，该操作被标记为危险")
        elif decision == PermissionDecision.ASK:
            warnings.append(f"ℹ 工具 {name} 命中 ask 规则，需要确认")

        # Bash 安全校验器警告
        if name == "bash":
            cmd = args.get("command") or args.get("cmd", "")
            for w in validate_bash_command(cmd):
                prefix = "⚠" if w.level == "danger" else "⚡"
                warnings.append(f"{prefix} {w.message}")

    # 构造审批选项
    options: list[dict] = []
    needs_permission_options = any(
        d
        in (
            PermissionDecision.DENY,
            PermissionDecision.UNMATCHED,
            PermissionDecision.ASK,
        )
        for d in decisions
    ) or bool(boundary_violations)

    if needs_permission_options:
        # 构造精确匹配和宽泛模式的工具表达式
        tc = last_message.tool_calls[0]
        exact_expr = build_exact_expr(tc["name"], tc.get("args", {}))
        pattern_expr = build_pattern_expr(tc["name"], tc.get("args", {}))

        options = [
            {"key": "allow_once", "label": "允许执行这一次"},
            {
                "key": "always_allow_exact",
                "label": f"始终允许: {exact_expr}",
                "tool_expr": exact_expr,
            },
        ]
        if pattern_expr and pattern_expr != exact_expr:
            options.append(
                {
                    "key": "always_allow_pattern",
                    "label": f"始终允许: {pattern_expr}",
                    "tool_expr": pattern_expr,
                }
            )
        options.append({"key": "reject", "label": "拒绝"})

    # 构造中断数据
    interrupt_data: dict = {
        "type": "tool_approval",
        "tool_calls": tool_calls_data,
        "decisions": [d.value for d in decisions],
    }

    # auto 模式：权限审批
    interrupt_data["message"] = "以下工具需要权限授权"
    if needs_permission_options:
        interrupt_data["options"] = options

    if warnings:
        interrupt_data["warnings"] = warnings
    if boundary_violations:
        interrupt_data["boundary_violations"] = boundary_violations

    decision_str = interrupt(interrupt_data)

    # 处理用户选择
    match decision_str:
        case "approve" | "allow_once":
            # 临时授权 boundary violation 路径（不持久化）
            for v in boundary_violations:
                add_authorized_directory(v)
            return Command(goto="ToolExecutor")
        case "always_allow_exact" | "always_allow_pattern":
            expr = next(
                (o["tool_expr"] for o in options if o["key"] == decision_str),
                None,
            )
            if expr:
                engine.add_allow_rule(expr)
            for v in boundary_violations:
                engine.add_workspace(v)
            return Command(goto="ToolExecutor")
        case "cancel":
            # esc 中断：模拟 ToolMessage 后直接结束
            cancel_messages = _build_reject_messages(
                last_message.tool_calls, content="用户中断了工具调用请求"
            )
            return Command(goto=END, update={"messages": cancel_messages})
        case _:
            # 拒绝：模拟 ToolMessage 后直接结束
            reject_messages = _build_reject_messages(last_message.tool_calls)
            return Command(goto=END, update={"messages": reject_messages})


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

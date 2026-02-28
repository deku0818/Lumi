from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
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
    offload_tool_result,
)
from lumi.agents.core.scheme import LumiAgentContext, LumiAgentState
from lumi.agents.core.structured_tool import (
    STRUCTURED_OUTPUT_INSTRUCTION,
    apply_output_enrich,
    create_structured_output_tool,
    extract_structured_args,
    is_structured_output_call,
)
from lumi.agents.base.response_service import extract_ainvoke_content
from lumi.utils.llm_chain import chat_chain, tiktoken_counter, tool_call_chain
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# 自带中断机制的工具，跳过审批直接执行
_APPROVAL_BYPASS_TOOLS = frozenset({"ask"})


async def call_model(state: LumiAgentState, runtime: Runtime[LumiAgentContext]):

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

    response = await chain.ainvoke({"messages": state["messages"]})

    if response.tool_calls:
        logger.debug(f"[SimpleAgent]正在进行第「{iterations}」次工具调用迭代")

    return {"messages": [response], "iterations": iterations + 1}


async def tool_executor(state: LumiAgentState, runtime: Runtime[LumiAgentContext]):
    """工具执行器，负责执行LLM调用的工具"""
    tools = runtime.context.tools

    tool_node = ToolNode(tools, handle_tool_errors=handle_tool_error)
    tool_messages = await tool_node.ainvoke({"messages": state["messages"]})

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

    # 4. 截断结果
    truncate_tool_results(messages_list)

    return {"messages": messages_list}


def is_use_tool(state: LumiAgentState):
    """
    条件路由函数 - 判断下一步执行哪个节点

    路由优先级：
    - 有 tool_calls 且包含结构化输出工具 → "ExtractStructuredOutput" 直接提取结构化数据
    - 有 tool_calls 且 tool_mode="auto" → "ToolExecutor" 直接执行
    - 有 tool_calls 且 tool_mode!="auto" → "HumanApproval" 等待人工审批
    - 无 tool_calls → "END" 结束流程

    注意：当 output_schema 存在时，call_model 会设置 tool_choice="any" 强制模型调用工具，
    因此不会出现"无 tool_calls + 有 output_schema"的情况。
    """
    last_message = state.get("messages", [])[-1]
    tool_calls = getattr(last_message, "tool_calls", [])

    if not tool_calls:
        return "END"

    if is_structured_output_call(tool_calls):
        return "ExtractStructuredOutput"
    if state.get("tool_mode", "") == "auto":
        return "ToolExecutor"
    if all(tc["name"] in _APPROVAL_BYPASS_TOOLS for tc in tool_calls):
        return "ToolExecutor"
    return "HumanApproval"


def human_approval(state: LumiAgentState) -> Command:
    """使用 interrupt 暂停执行，等待用户审批

    中断时返回结构化数据，包含：
    - type: 中断类型，固定为 "tool_approval"
    - message: 提示信息
    - tool_calls: 待审批的工具调用列表
    """
    last_message = state["messages"][-1]

    decision = interrupt(
        {
            "type": "tool_approval",
            "message": "是否执行以下工具？",
            "tool_calls": [
                {"name": tc["name"], "args": tc["args"]}
                for tc in last_message.tool_calls
            ],
        }
    )

    if decision == "approve":
        return Command(goto="ToolExecutor")
    if decision == "auto":
        return Command(goto="ToolExecutor", update={"tool_mode": "auto"})
    return Command(goto=END)


async def extract_structured_output(state: LumiAgentState):
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


async def summarizer(state: LumiAgentState):
    """总结历史聊天消息，记录摘要信息到 state（不直接替换）

    此函数在后台运行，与 CallModel 并行执行。
    生成的摘要会在下一轮对话时由 preprocess_messages 执行实际替换。

    触发条件：
    - 消息 token 数 >= model_max_tokens * summary_threshold

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
    threshold = token_config.model_max_tokens * token_config.summary_threshold
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

    # 5. 生成摘要（直接使用 LLM chain，避免递归）
    prompt = get_config().load_prompt("SUMMARY")
    if prompt is None:
        prompt = get_config().load_prompt("summary")
        if prompt:
            logger.warning(
                "使用 summary.md 作为摘要提示词已废弃，请将文件重命名为 SUMMARY.md。"
            )
    if not prompt:
        raise ValueError(
            "未找到摘要提示词配置 'SUMMARY.md'。\n"
            "请在 .lumi/prompts/SUMMARY.md 中配置摘要提示词。"
        )
    summary_messages = messages_to_summarize + [HumanMessage(content=prompt)]
    chain = chat_chain(
        system_prompt="你是一个智能总结助手，请遵循我的指令进行准确完善的总结。",
        temperature=1,
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


async def preprocess_messages(state: LumiAgentState):
    """消息预处理节点，在调用模型前执行以下操作:

    0. 检查并执行摘要替换（如果 state["summary"] 有值）
    1. 清理不完整的工具调用
    2. 卸载大工具结果到文件系统
    """
    messages = state["messages"]
    result_messages = []

    # 0. 检查并执行摘要替换
    summary_data = state.get("summary", {})
    if summary_data and summary_data.get("summarized_ids"):
        summarized_ids = summary_data["summarized_ids"]
        summary_text = summary_data["summary_text"]

        # 第一个 ID 用于摘要消息（替换位置），其他 ID 删除
        first_id = summarized_ids[0]
        ids_to_remove = summarized_ids[1:]

        for msg_id in ids_to_remove:
            result_messages.append(RemoveMessage(id=msg_id))

        summary_content = f"[历史对话摘要]\n{summary_text}\n\n[以下是最近的对话]"
        result_messages.append(HumanMessage(content=summary_content, id=first_id))

        logger.info(f"[PreprocessMessages] 已替换 {len(summarized_ids)} 条消息为摘要")
        return {"messages": result_messages, "summary": {}}

    # 1. 清理不完整的工具调用
    result_messages.extend(cleanup_incomplete_tool_calls(messages))

    # 2. 卸载大工具结果到本地文件系统
    result_messages.extend(await offload_tool_result(messages))

    if result_messages:
        return {"messages": result_messages}
    return {"messages": []}

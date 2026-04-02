from typing import Any, Literal, overload

import anthropic
import httpx
import openai
from langchain_anthropic import ChatAnthropic
from langchain_core.documents import Document
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
)
from pydantic import BaseModel

from lumi.agents.core.node_helpers.messages import CACHE_CONTROL
from lumi.utils.logger import logger
from lumi.utils.model_manager import get_default_model_name
from lumi.utils.model_manager import create_llm, detect_model_type
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import str_token_counter


@overload
def truncate_docs_to_max_tokens(
    docs: list[Document], max_tokens: int = 10000
) -> list[Document]: ...


@overload
def truncate_docs_to_max_tokens(
    docs: list[str], max_tokens: int = 10000
) -> list[str]: ...


@overload
def truncate_docs_to_max_tokens(docs: str, max_tokens: int = 10000) -> str: ...


def truncate_docs_to_max_tokens(
    docs: list[Document] | list[str] | str, max_tokens: int = 10000
) -> list[Document] | list[str] | str:
    """
    截取文档集合、字符串列表或单个字符串到指定的最大token数量。
    对于列表类型,只返回能够完整包含的项目,不会截断单个项目。
    对于单个字符串,会按照token截断。

    Args:
        docs: 文档集合、字符串列表或单个字符串
        max_tokens: 最大允许的token数量，默认为10000

    Returns:
        截取后的文档集合、字符串列表或字符串，类型与输入保持一致

    Raises:
        ValueError: 当 max_tokens 小于等于 0 时抛出异常
    """
    # 处理单个字符串的情况
    if isinstance(docs, str):
        if max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")

        if not docs:
            return docs

        # 先检查是否需要截断
        current_tokens = str_token_counter(docs)
        if current_tokens <= max_tokens:
            logger.debug(
                f"字符串处理完成 - token数: {current_tokens}/{max_tokens} (无需截断)"
            )
            return docs

        # 需要截断，使用tiktoken进行截断
        import tiktoken

        enc = tiktoken.encoding_for_model("gpt-4")
        tokens = enc.encode(docs)
        truncated_tokens = tokens[:max_tokens]
        truncated_str = enc.decode(truncated_tokens)

        logger.debug(
            f"字符串处理完成 - 原始token数: {current_tokens}, 截断后token数: {len(truncated_tokens)}/{max_tokens}"
        )
        return truncated_str

    # 处理列表的情况
    if not docs:
        return []

    truncated_items = []
    current_tokens = 0

    for item in docs:
        # 根据输入类型获取文本内容
        if isinstance(item, Document):
            text_content = item.page_content
        elif isinstance(item, str):
            text_content = item
        else:
            # 对于不支持的类型，直接转换为字符串进行处理
            text_content = str(item)

        item_tokens = str_token_counter(text_content)

        # 如果单个项目就超过最大token限制，直接跳过
        if item_tokens > max_tokens:
            continue

        # 如果添加当前项目后会超出限制，就停止添加
        if current_tokens + item_tokens > max_tokens:
            break

        truncated_items.append(item)
        current_tokens += item_tokens

    logger.debug(
        f"内容处理完成 - 原始数量: {len(docs)}, "
        f"截取后数量: {len(truncated_items)}, "
        f"总token数: {current_tokens}/{max_tokens}"
    )

    return truncated_items


# 图片在 token 计算中的固定估算值（避免 base64 数据膨胀 token 计数）
IMAGE_TOKEN_ESTIMATE = 800


def _count_content_tokens(content) -> int:
    """计算消息 content 的 token 数（图片使用固定估算值，不对 base64 数据进行 tokenize）"""
    if isinstance(content, str):
        return str_token_counter(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    total += str_token_counter(item.get("text", ""))
                elif item.get("type") in ("image", "image_url"):
                    total += IMAGE_TOKEN_ESTIMATE
                else:
                    total += str_token_counter(str(item))
            else:
                total += str_token_counter(str(item))
        return total
    return str_token_counter(str(content))


def tiktoken_counter(messages: list[BaseMessage]) -> int:
    """估算消息列表的 token 总数

    支持 str 和多模态 list content（图片使用固定估算值 IMAGE_TOKEN_ESTIMATE）。
    """
    num_tokens = 3  # every reply is primed with <|start|>assistant<|message|>
    tokens_per_message = 3
    tokens_per_name = 1
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, ToolMessage):
            role = "tool"
        elif isinstance(msg, SystemMessage):
            role = "system"
        else:
            raise ValueError(f"Unsupported messages type {msg.__class__}")
        content_tokens = _count_content_tokens(msg.content)
        num_tokens += tokens_per_message + str_token_counter(role) + content_tokens
        if msg.name:
            num_tokens += tokens_per_name + str_token_counter(msg.name)
    return num_tokens


def my_trim_messages(max_tokens: int = None):
    """通用工具：创建一个消息修剪器，用于修剪消息。

    Args:
        max_tokens: 最大允许的token数量，如果为None则使用配置文件中的默认值
    """
    # 如果没有提供max_tokens，则从配置文件中获取
    if max_tokens is None:
        max_tokens = get_config().config.token.trim_messages_max_tokens

    return trim_messages(
        token_counter=tiktoken_counter,
        strategy="last",
        max_tokens=max_tokens,
        start_on="human",
        end_on=("human", "tool"),
        include_system=True,
    )


def structured_output(
    template: str,
    structure: dict[str, Any] | type[BaseModel],
    structure_method: Literal[
        "json_schema", "function_calling", "json_mode"
    ] = "function_calling",
    system_prompt: str | None = None,
    model_name: str = None,
    use_cache: bool = True,
    **llm_params,
):
    """通用工具：创建一个结构化输出链，用于解析用户的输入。

    Args:
        template: 提示模板
        structure: 输出结构定义
        structure_method: 结构化方法，默认使用function_calling
        system_prompt: 系统提示信息
        model_name: 指定使用的模型名称，如果为None则使用环境变量
        use_cache: 是否使用LLM缓存，默认为True
        **llm_params: LLM的其他参数

    Returns:
        chain: 结构化输出链
    """
    # 设置默认参数
    default_llm_params = {
        "streaming": False,
        "temperature": 0,
        "timeout": 120,
        "max_tokens": 2000,
    }
    default_llm_params.update(llm_params)

    # Anthropic / Bedrock 模型禁用 thinking 以避免与 structured_output 冲突
    if detect_model_type(model_name or get_default_model_name()) in (
        "anthropic",
        "bedrock",
    ):
        default_llm_params["thinking"] = None

    llm = create_llm(model_name=model_name, use_cache=use_cache, **default_llm_params)

    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))

    messages.extend(
        [
            MessagesPlaceholder("chat_history", optional=True),
            HumanMessagePromptTemplate.from_template(template),
        ]
    )

    prompt = ChatPromptTemplate.from_messages(messages)

    structured_llm = llm.with_structured_output(structure, method=structure_method)
    chain = prompt | my_trim_messages() | structured_llm
    chain = chain.with_retry(
        stop_after_attempt=5,
        retry_if_exception_type=(
            openai.APIError,
            anthropic.APIError,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
        ),
        wait_exponential_jitter=True,
        exponential_jitter_params={"initial": 15, "max": 300},
    )
    return chain


def tool_call_chain(
    tools: list,
    system_prompt: str | None = None,
    model_name: str = None,
    use_cache: bool = True,
    tool_choice: str | dict | None = None,
    **llm_params,
):
    """
    创建一个工具调用链，用于调用工具。

    Args:
        tools: 工具列表
        system_prompt: 系统提示信息
        model_name: 指定使用的模型名称
        use_cache: 是否使用LLM缓存
        tool_choice: 指定 tool_choice。注意 Anthropic 和 OpenAI 的取值不同：
            强制调用任意工具: Anthropic 用 "any"，OpenAI 用 "required"；
            强制调用指定工具: Anthropic 用 {"type": "tool", "name": "xxx"}，
            OpenAI 用 {"type": "function", "function": {"name": "xxx"}}
            但是langchain自行做了适配，所以传入的值会自动转换为适合的值
        **llm_params: LLM的其他参数
    """
    # 设置默认参数
    default_llm_params = {"streaming": True, "temperature": 1, "timeout": 300}
    default_llm_params.update(llm_params)

    # Anthropic: thinking 与强制 tool_choice 不兼容，需禁用 thinking
    if tool_choice is not None and detect_model_type(
        model_name or get_default_model_name()
    ) in (
        "anthropic",
        "bedrock",
    ):
        default_llm_params["thinking"] = None
        use_cache = False

    llm = create_llm(model_name=model_name, use_cache=use_cache, **default_llm_params)

    if tool_choice is not None:
        llm_with_tools = llm.bind_tools(tools, tool_choice=tool_choice)
    else:
        llm_with_tools = llm.bind_tools(tools)

    messages = []
    if system_prompt:
        if isinstance(llm, ChatAnthropic):
            messages.append(
                SystemMessage(
                    content=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": CACHE_CONTROL,
                        }
                    ]
                )
            )
        else:
            messages.append(SystemMessage(content=system_prompt))

    messages.append(MessagesPlaceholder(variable_name="messages"))

    prompt = ChatPromptTemplate.from_messages(messages)
    chain = prompt | my_trim_messages() | llm_with_tools

    # 流式 chain：只 retry 连接前的错误（rate limit 等），
    # 不 retry 流式中途断开的 httpx 错误（会导致 TUI 重复输出）
    chain = chain.with_retry(
        stop_after_attempt=5,
        retry_if_exception_type=(openai.APIError, anthropic.APIError),
        wait_exponential_jitter=True,
        exponential_jitter_params={"initial": 15, "max": 300},
    )
    return chain


def chat_chain(
    template: str | None = None,
    system_prompt: str | None = None,
    model_name: str = None,
    use_cache: bool = True,
    **llm_params,
):
    """通用工具：创建一个聊天链，用于与用户进行对话。

    Args:
        template: 聊天模板，当使用multimodal_content时可以为None
        system_prompt: 系统提示信息
        model_name: 指定使用的模型名称，如果为None则使用环境变量
        use_cache: 是否使用LLM缓存，默认为True
        **llm_params: LLM的参数
    """
    # 设置默认参数
    default_llm_params = {"streaming": True, "temperature": 0.6, "timeout": 120}
    default_llm_params.update(llm_params)

    llm = create_llm(model_name=model_name, use_cache=use_cache, **default_llm_params)

    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))

    messages.append(MessagesPlaceholder(variable_name="messages", optional=True))

    if template:
        messages.append(HumanMessagePromptTemplate.from_template(template))

    prompt = ChatPromptTemplate.from_messages(messages)
    chain = prompt | my_trim_messages() | llm

    chain = chain.with_retry(
        stop_after_attempt=5,
        retry_if_exception_type=(
            openai.APIError,
            anthropic.APIError,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
        ),
        wait_exponential_jitter=True,
        exponential_jitter_params={"initial": 15, "max": 300},
    )
    return chain

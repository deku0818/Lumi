"""LLM chain 工厂：结构化输出链与工具调用链。"""

from typing import Any, Literal

import anthropic
import httpx
import openai
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, trim_messages
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
)
from pydantic import BaseModel

from lumi.models.cache import CACHE_CONTROL
from lumi.models.manager import create_llm
from lumi.utils.read_config import get_config
from lumi.utils.sizing import content_size, estimate_tokens

# 仅重试真正瞬态的错误：限流、5xx、连接/超时（APIConnectionError 含 Timeout 子类）。
# 不能用宽泛的 APIError——它包含 4xx 客户端错误（如模型不支持某参数的 400），
# 重试只会在指数退避里"卡住"数分钟，正确行为是立即失败并把错误透传给用户。
# 流式 chain 不重试中途断开的 httpx 错误，否则会导致 TUI 重复输出。
_API_ERRORS = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)
_API_AND_NETWORK_ERRORS = _API_ERRORS + (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
)


def _with_retry(chain, retry_errors: tuple):
    return chain.with_retry(
        stop_after_attempt=5,
        retry_if_exception_type=retry_errors,
        wait_exponential_jitter=True,
        exponential_jitter_params={"initial": 15, "max": 300},
    )


def _estimate_message_tokens(messages: list) -> int:
    """trim_messages 用的轻量 token 估算（字节 ÷ BYTES_PER_TOKEN）。

    trim 会反复在任意候选子集上调用 token_counter，故不能用只对完整列表成立的
    usage；这里按字节粗估，多模态 block 走固定字节当量、不对 base64 计长，每条加
    固定角色/分隔开销。精度不敏感：trim 是带 headroom 的安全阀，偏差后续有 summary 兜底。
    """
    num = 3  # 回复 priming
    for msg in messages:
        num += 4 + estimate_tokens(content_size(getattr(msg, "content", "")))
    return num


def my_trim_messages(max_tokens: int | None = None):
    """通用工具：创建一个消息修剪器，用于修剪消息。

    Args:
        max_tokens: 最大允许的token数量，如果为None则使用配置文件中的默认值
    """
    if max_tokens is None:
        max_tokens = get_config().config.token.trim_messages_max_tokens

    return trim_messages(
        token_counter=_estimate_message_tokens,
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
    model_name: str | None = None,
    use_cache: bool = True,
    **llm_params,
):
    """通用工具：创建一个结构化输出链，用于解析用户的输入。

    Args:
        template: 提示模板
        structure: 输出结构定义
        structure_method: 结构化方法，默认使用function_calling
        system_prompt: 系统提示信息
        model_name: 指定使用的模型名称，如果为None则使用当前 active 模型
        use_cache: 是否使用LLM缓存，默认为True
        **llm_params: LLM的其他参数

    Returns:
        chain: 结构化输出链
    """
    # 结构化输出走 with_structured_output（function_calling）会强制 tool_choice，
    # 与思考模式不兼容（默认常开思考的模型如 qwen toggle 型会 400）——主动关闭思考。
    llm = create_llm(
        model_name=model_name,
        use_cache=use_cache,
        force_no_thinking=True,
        **llm_params,
    )

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
    return _with_retry(chain, _API_AND_NETWORK_ERRORS)


def tool_call_chain(
    tools: list,
    system_prompt: str | None = None,
    model_name: str | None = None,
    use_cache: bool = True,
    tool_choice: str | dict | None = None,
    apply_effort: bool = False,
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
        apply_effort: 注入当前模型的思考档位（仅主对话链传 True）
        **llm_params: LLM的其他参数
    """
    # streaming 是功能性标志：TUI / desktop 的逐 token 输出依赖它
    default_llm_params = {"streaming": True}
    default_llm_params.update(llm_params)

    # 强制 tool_choice 与思考不兼容：Anthropic 直接 400，默认常开思考的模型
    # （qwen toggle 型）报「tool_choice 不支持 required/object in thinking mode」。
    # 仅「不注入档位」对默认常开思考的模型不够，须主动关闭。
    force_no_thinking = tool_choice is not None
    if force_no_thinking:
        apply_effort = False

    llm = create_llm(
        model_name=model_name,
        use_cache=use_cache,
        apply_effort=apply_effort,
        force_no_thinking=force_no_thinking,
        **default_llm_params,
    )

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
    return _with_retry(chain, _API_ERRORS)

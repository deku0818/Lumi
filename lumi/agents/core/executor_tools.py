"""工具执行器模块

提供工具执行相关的辅助函数，包括：
- 工具结果截断
- JSON 提取与修复
- 工具错误处理
"""

import json
import re

from json_repair import repair_json
from jsonschema import ValidationError, validate

from lumi.utils.llm_chain import structured_output, truncate_docs_to_max_tokens
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config


def truncate_tool_results(messages_list: list) -> list:
    """截断工具返回结果

    确保工具调用结果不超过最大 token 数。
    截断后会在内容末尾添加"已被截断"的提示。

    Args:
        messages_list: 工具消息列表

    Returns:
        截断后的消息列表
    """
    max_tokens = get_config().config.token.once_tool_max_tokens

    for msg in messages_list:
        if not hasattr(msg, "content"):
            continue
        try:
            original_content = msg.content
            truncated_content = truncate_docs_to_max_tokens(
                original_content, max_tokens=max_tokens
            )
            # 如果内容被截断，添加提示
            if truncated_content != original_content:
                truncated_content += "\n\n... [内容已被截断]"
            msg.content = truncated_content
        except json.JSONDecodeError as e:
            content_preview = (
                msg.content[:200] + "..."
                if len(str(msg.content)) > 200
                else msg.content
            )
            logger.warning(
                f"工具执行完成，但截断失败 (JSONDecodeError: {e.msg}). "
                f"内容预览: {content_preview}"
            )

    return messages_list


def try_extract_json(content: str, schema: dict | None = None) -> dict | None:
    """尝试从大模型输出中提取并修复 JSON

    处理流程：
    1. 去除 ```json ``` 或 ``` 等代码块包裹
    2. 使用 json-repair 修复可能损坏的 JSON
    3. 如果提供了 schema，验证 JSON 是否符合 schema

    Args:
        content: 大模型的原始输出内容
        schema: 可选的 JSON Schema，用于验证提取的 JSON

    Returns:
        解析并验证通过的 dict，如果失败返回 None
    """
    if not content or not isinstance(content, str):
        return None

    # 1. 尝试提取 JSON 代码块
    # 匹配 ```json ... ``` 或 ``` ... ``` 包裹的内容
    code_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
    matches = re.findall(code_block_pattern, content)

    # 候选内容列表：优先使用代码块内容，其次使用原始内容
    candidates = matches + [content]

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        try:
            # 2. 使用 json-repair 修复 JSON
            repaired = repair_json(candidate, return_objects=True)

            # 确保结果是 dict 类型
            if not isinstance(repaired, dict):
                continue

            # 3. 如果提供了 schema，验证 JSON
            if schema:
                # 移除 LangChain 添加的顶层字段，避免验证失败
                schema_for_validation = {
                    k: v for k, v in schema.items() if k not in ("title", "description")
                }
                try:
                    validate(instance=repaired, schema=schema_for_validation)
                except ValidationError:
                    continue

            logger.debug("[TryExtractJSON] 成功从模型输出中提取 JSON")
            return repaired

        except Exception:
            continue

    return None


async def extract_json_with_llm(
    content: str,
    schema: dict,
    model_name: str | None = None,
) -> dict:
    """使用 LLM 从内容中提取符合 schema 的 JSON

    当 try_extract_json 无法直接提取时，使用此函数调用 LLM 进行结构化输出。

    Args:
        content: 需要提取的原始内容
        schema: JSON Schema，定义输出结构
        model_name: 指定使用的模型名称

    Returns:
        符合 schema 的 dict

    Raises:
        Exception: LLM 调用失败时抛出异常
    """

    template = """请根据以下内容，提取出符合指定 JSON Schema 的结构化数据。

## 内容
{content}

## 要求
- 仅输出符合 schema 的 JSON 数据
- 如果某些字段在内容中找不到，根据类型设置合理的默认值（字符串用空字符串，数组用空数组）
- 确保输出的 JSON 格式正确"""

    chain = structured_output(
        template=template,
        structure=schema,
        model_name=model_name,
        system_prompt="你是一个精确的数据提取助手，严格按照 JSON Schema 输出结构化数据。",
        temperature=1,
        max_tokens=16000,
    )

    return await chain.ainvoke({"content": content})


def handle_tool_error(error: Exception) -> str:
    """处理工具执行异常，返回友好的错误消息

    这个函数会被 ToolNode 内部调用，为每个失败的工具单独处理异常。
    这样可以确保多个工具并发执行时，一个失败不会影响其他工具的结果。

    Args:
        error: 异常对象

    Returns:
        友好的错误消息
    """
    error_message = str(error)
    logger.error(f"[ToolExecutor] 工具执行失败: {error_message}")

    # 如果是搜索工具无结果的情况，提供更友好的提示
    if (
        "No search results available" in error_message
        or "no search results" in error_message.lower()
    ):
        return "此关键词未找到相关搜索结果"

    # 返回通用错误消息
    return f"工具执行失败: {error_message}"

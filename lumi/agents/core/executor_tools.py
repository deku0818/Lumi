"""工具执行器模块

提供工具执行相关的辅助函数，包括：
- 工具结果截断与卸载
- JSON 提取与修复
- 工具错误处理
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from json_repair import repair_json
from jsonschema import ValidationError, validate

from lumi.utils.llm_chain import structured_output, truncate_docs_to_max_tokens
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import str_token_counter

# 使用截断+分段读取提示的工具（这些工具自身支持 offset/limit 分段读取）
_TRUNCATE_ONLY_TOOLS: frozenset[str] = frozenset({"read"})


def _content_to_str(content: str | list | object) -> str:
    """将消息 content 转换为纯文本字符串。

    Args:
        content: ToolMessage 的 content，可能是 str、list[dict] 或其他类型

    Returns:
        纯文本字符串
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _build_truncation_summary(
    content_str: str,
    truncated_str: str,
    max_tokens: int,
) -> str:
    """构建截断元信息摘要。

    Args:
        content_str: 原始完整文本
        truncated_str: 截断后文本
        max_tokens: 单次工具最大 token 数

    Returns:
        格式化的截断摘要文本
    """
    token_count = str_token_counter(content_str)
    truncated_token_count = str_token_counter(truncated_str)
    remaining_tokens = token_count - truncated_token_count
    line_count = content_str.count("\n") + 1
    truncated_line_count = truncated_str.count("\n") + 1
    remaining_lines = line_count - truncated_line_count

    return (
        f"... [内容已被截断]\n"
        f"已显示：{truncated_token_count} tokens, {truncated_line_count} 行\n"
        f"剩余：{remaining_tokens} tokens, {remaining_lines} 行\n"
        f"原始：{len(content_str)} 字符, {token_count} tokens, {line_count} 行\n"
        f"单次工具最大 {max_tokens} tokens"
    )


def _write_offload_file(file_path: Path, content: str) -> None:
    """将内容写入卸载文件（同步，供 asyncio.to_thread 调用）。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


async def truncate_tool_results(messages_list: list) -> list:
    """截断或卸载工具返回结果。

    当工具调用结果超过最大 token 数时，根据工具类型采取不同策略：
    - read 等支持分段读取的工具：截断并提示使用 offset/limit 分段读取
    - 其他工具：优先将完整内容卸载到本地文件系统，消息中保留文件路径引用；
      卸载失败时回退到截断

    Args:
        messages_list: 工具消息列表

    Returns:
        处理后的消息列表
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

            # 内容未超限，无需处理
            if truncated_content == original_content:
                continue

            content_str = _content_to_str(original_content)
            truncated_str = _content_to_str(truncated_content)
            tool_name = getattr(msg, "name", "unknown")

            # read 工具：始终截断并附带分段读取提示
            if tool_name in _TRUNCATE_ONLY_TOOLS:
                summary = _build_truncation_summary(
                    content_str, truncated_str, max_tokens
                )
                msg.content = (
                    f"{truncated_str}\n\n{summary}\n"
                    f"可使用 offset 和 limit 参数分段读取剩余内容。"
                )
                continue

            # 其他工具：尝试卸载到文件系统
            offloaded = await _try_offload_to_file(tool_name, content_str, max_tokens)
            if offloaded:
                msg.content = offloaded
            else:
                # 卸载失败，回退到截断
                summary = _build_truncation_summary(
                    content_str, truncated_str, max_tokens
                )
                msg.content = f"{truncated_str}\n\n{summary}"

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


async def _try_offload_to_file(
    tool_name: str,
    content_str: str,
    max_tokens: int,
) -> str | None:
    """尝试将工具结果卸载到本地文件系统。

    Args:
        tool_name: 工具名称
        content_str: 完整的工具返回文本
        max_tokens: 单次工具最大 token 数

    Returns:
        卸载成功时返回替换消息文本，失败时返回 None
    """
    token_count = str_token_counter(content_str)
    line_count = content_str.count("\n") + 1

    timestamp = datetime.now().strftime("%H%M%S%f")
    file_name = f"{tool_name}_result_{timestamp}.txt"
    offload_dir = get_config().config_dir / "offload"
    file_path = offload_dir / file_name

    try:
        await asyncio.to_thread(_write_offload_file, file_path, content_str)

        logger.info(
            f"[truncate_tool_results] {tool_name} 结果已卸载到 "
            f"{file_path} (原始 {token_count} tokens)"
        )
        return (
            f"工具返回内容过大，已卸载到文件：{file_path}\n"
            f"文件信息：\n"
            f"{len(content_str)} 字符, {token_count} tokens, {line_count} 行\n"
            f"单次工具最大 {max_tokens} tokens\n"
            f"请使用 read 分段读取或 grep 搜索关键内容。"
        )
    except Exception as e:
        logger.warning(
            f"[truncate_tool_results] 写入文件失败: {type(e).__name__}: {e}，回退到截断"
        )
        return None


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

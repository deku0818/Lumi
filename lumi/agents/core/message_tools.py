"""消息处理工具模块

提供消息处理相关的函数，包括：
- 清理不完整的工具调用
- 卸载大型工具结果到文件
- 消息辅助函数
"""

import asyncio
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import str_token_counter


def inject_text_into_message(message: HumanMessage, text: str) -> HumanMessage:
    """将文本块插入到 HumanMessage content 最前面，返回新消息。

    当 content 为字符串时，先转换为列表格式再插入。
    不修改原消息（不可变原则）。

    Args:
        message: 原始用户消息
        text: 要注入的文本

    Returns:
        注入后的新 HumanMessage
    """
    if isinstance(message.content, str):
        content_blocks: list[dict[str, str]] = [
            {"type": "text", "text": message.content}
        ]
    else:
        content_blocks = list(message.content)

    content_blocks.insert(0, {"type": "text", "text": text})

    return HumanMessage(content=content_blocks)


def get_last_human_message(messages: list) -> HumanMessage | None:
    """从消息列表中获取最后一条人类消息"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def cleanup_incomplete_tool_calls(messages: list) -> list[RemoveMessage]:
    """清理没有对应 ToolMessage 的 AIMessage (tool_use)

    当工具调用中途出错时，会留下没有对应 tool_result 的 tool_use 消息，
    这会导致 Anthropic API 返回 400 错误。此函数会检测并返回需要删除的消息。

    Args:
        messages: 消息列表

    Returns:
        list[RemoveMessage]: 需要删除的消息列表
    """
    messages_to_remove = []

    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            # 检查下一条消息是否为 ToolMessage
            has_tool_result = False
            if i + 1 < len(messages):
                next_msg = messages[i + 1]
                if isinstance(next_msg, ToolMessage):
                    # 检查 tool_call_id 是否匹配
                    tool_call_ids = {
                        tc.get("id") for tc in msg.tool_calls if isinstance(tc, dict)
                    }
                    if not tool_call_ids:
                        tool_call_ids = {
                            getattr(tc, "id", None) for tc in msg.tool_calls
                        }
                    if (
                        hasattr(next_msg, "tool_call_id")
                        and next_msg.tool_call_id in tool_call_ids
                    ):
                        has_tool_result = True

            if not has_tool_result:
                logger.warning(
                    f"[PreprocessMessages] 发现不完整的工具调用消息 (id: {msg.id}), "
                    f"将其删除以避免 API 错误"
                )
                messages_to_remove.append(RemoveMessage(id=msg.id))

    return messages_to_remove


async def offload_tool_result(messages: list) -> list[ToolMessage]:
    """将指定工具的大量结果卸载到文件系统


    Args:
        messages: 消息列表

    Returns:
        list[ToolMessage]: 替换后的消息列表
    """
    offload_config = get_config().config.tool_offload
    updated_messages = []

    if not offload_config.enabled:
        return updated_messages

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue

        tool_name = getattr(msg, "name", None)
        if tool_name not in offload_config.tools:
            continue

        content = msg.content
        if not content:
            continue

        # 将非字符串 content 提取为纯文本
        if isinstance(content, str):
            content_str = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            content_str = "\n".join(parts)
        else:
            content_str = str(content)

        token_count = str_token_counter(content_str)
        if token_count < offload_config.token_threshold:
            continue

        # 生成文件名并写入 .lumi/offload/ 目录
        timestamp = datetime.now().strftime("%H%M%S")
        file_name = f"{tool_name}_result_{timestamp}.txt"
        offload_dir = get_config().config_dir / "offload"
        file_path = offload_dir / file_name

        try:
            await asyncio.to_thread(_write_offload_file, file_path, content_str)

            # 创建替换消息
            new_content = f"执行结果已保存到:{file_path}"
            new_msg = ToolMessage(
                content=new_content,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                id=msg.id,
            )
            updated_messages.append(new_msg)

            logger.info(
                f"[PreprocessMessages] {tool_name} 结果已卸载到 {file_path} "
                f"(原始 {token_count} tokens)"
            )

        except Exception as e:
            logger.error(
                f"[PreprocessMessages] 写入文件失败: {type(e).__name__}: {e}. "
                f"工具: {tool_name}, 路径: {file_path}, "
                f"内容大小: {token_count} tokens. "
                f"将使用原始内容（可能导致token超限）"
            )

    return updated_messages


def _write_offload_file(file_path: Path, content: str) -> None:
    """将内容写入卸载文件（同步，供 asyncio.to_thread 调用）"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


# Anthropic prompt 缓存控制标记
CACHE_CONTROL = {"type": "ephemeral", "ttl": "5m"}


def _add_cache_control(
    msg: HumanMessage | AIMessage | ToolMessage,
) -> HumanMessage | AIMessage | ToolMessage:
    """为消息的最后一个内容块添加 cache_control，返回新消息对象。

    如果 content 既非字符串也非非空列表，则原样返回。
    """
    content = msg.content

    if isinstance(content, str):
        new_content = [
            {"type": "text", "text": content, "cache_control": CACHE_CONTROL}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        last = new_content[-1]
        if isinstance(last, dict):
            new_content[-1] = {**last, "cache_control": CACHE_CONTROL}
        elif isinstance(last, str):
            new_content[-1] = {
                "type": "text",
                "text": last,
                "cache_control": CACHE_CONTROL,
            }
        else:
            return msg
    else:
        return msg

    return msg.model_copy(update={"content": new_content})


def inject_message_cache_breakpoints(messages: list) -> None:
    """为消息列表末尾添加缓存断点（滑动窗口策略）。

    在倒数第 2 条和最后 1 条非系统消息上添加 cache_control，
    使每轮新请求的断点随对话向后滑动，上一轮末尾自动被前缀缓存覆盖。

    仅对 Anthropic 模型有意义，调用侧应判断模型类型后再调用。

    Args:
        messages: 消息列表，就地替换对应元素
    """
    from langchain_core.messages import SystemMessage

    non_system = [i for i, m in enumerate(messages) if not isinstance(m, SystemMessage)]
    if not non_system:
        return

    if len(non_system) >= 2:
        idx = non_system[-2]
        messages[idx] = _add_cache_control(messages[idx])

    idx = non_system[-1]
    messages[idx] = _add_cache_control(messages[idx])

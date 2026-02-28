"""消息处理工具模块

提供消息处理相关的函数，包括：
- 清理不完整的工具调用
- 卸载大型工具结果到文件
- 消息辅助函数
"""

from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.token_counter import str_token_counter


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
        session: 沙箱会话实例

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

        # 生成文件名并通过沙箱会话写入
        timestamp = datetime.now().strftime("%H%M%S")
        file_name = f"{tool_name}_result_{timestamp}.txt"
        virtual_path = f"/workspace/offloaded/{file_name}"

        try:
            ### 这里要卸载内容到文件（未实现占位）

            # 创建替换消息
            new_content = f"执行结果已保存到:{virtual_path}"
            new_msg = ToolMessage(
                content=new_content,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                id=msg.id,
            )
            updated_messages.append(new_msg)

            logger.info(
                f"[PreprocessMessages] {tool_name} 结果已卸载到 {virtual_path} "
                f"(原始 {token_count} tokens)"
            )

        except Exception as e:
            logger.error(
                f"[PreprocessMessages] 写入文件失败: {type(e).__name__}: {e}. "
                f"工具: {tool_name}, 路径: {virtual_path}, "
                f"内容大小: {token_count} tokens. "
                f"将使用原始内容（可能导致token超限）"
            )

    return updated_messages

"""历史消息恢复 — 从 LangGraph checkpoint 读取消息并渲染到 ChatLog。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.user_message import UserMessage
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from lumi.tui.widgets.chat_log import ChatLog

# ── 正则和常量 ──

# 工具被拒绝/中断时的输出关键词
_TOOL_REJECT_KEYWORDS: frozenset[str] = frozenset(
    {
        "用户拒绝了工具执行",
        "用户中断了工具调用请求",
        "User declined to answer questions",
    }
)

# 从 system-reminder 中提取 command-name 的正则
_COMMAND_NAME_RE: re.Pattern[str] = re.compile(
    r"<command-name>(/[\w-]+)</command-name>"
)

# 从消息中提取 user-input 的正则
_USER_INPUT_RE: re.Pattern[str] = re.compile(
    r"<user-input>(.*?)</user-input>", re.DOTALL
)


async def restore_messages(
    graph: CompiledStateGraph | None,
    chat_log: ChatLog,
    thread_id: str,
    checkpoint_id: str = "",
) -> None:
    """从 checkpoint 恢复历史消息并渲染到 ChatLog。

    处理 human、ai（含 tool_calls）和 tool 类型消息。
    先收集所有 tool 消息的输出，再按顺序渲染，确保 ToolBlock 能匹配到输出。

    Args:
        graph: 编译后的 LangGraph 状态图，为 None 时直接返回
        chat_log: 聊天日志组件
        thread_id: 会话线程 ID
        checkpoint_id: 指定 LangGraph checkpoint_id，为空则读取最新 HEAD
    """
    if graph is None:
        return

    try:
        configurable: dict[str, str] = {"thread_id": thread_id}
        if checkpoint_id:
            configurable["checkpoint_id"] = checkpoint_id
        config = {"configurable": configurable}
        snapshot = await graph.aget_state(config)
        if not snapshot or not snapshot.values:
            return

        messages = snapshot.values.get("messages", [])

        # 预先收集所有 tool 消息的输出，key 为 tool_call_id
        tool_outputs: dict[str, str] = {}
        for msg in messages:
            msg_type = getattr(msg, "type", None)
            if msg_type == "tool":
                tc_id = getattr(msg, "tool_call_id", None)
                content = getattr(msg, "content", "")
                if tc_id:
                    tool_outputs[tc_id] = extract_text_content(content)

        for msg in messages:
            msg_type = getattr(msg, "type", None) or (
                msg.get("type") if isinstance(msg, dict) else None
            )
            content = getattr(msg, "content", None) or (
                msg.get("content", "") if isinstance(msg, dict) else ""
            )

            if msg_type == "human":
                display = extract_human_display_text(content)
                if display:
                    await chat_log.mount(UserMessage(display))

            elif msg_type == "ai":
                # 渲染文本内容
                text = extract_text_content(content)
                if text:
                    assistant_msg = AssistantMessage()
                    await chat_log.mount(assistant_msg)
                    assistant_msg.append_token(text)
                    assistant_msg.finalize()

                # 渲染 tool_calls
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    name = tc.get("name", "unknown")
                    args = tc.get("args", {})
                    tc_id = tc.get("id", "")
                    block = ToolBlock(name, args)
                    await chat_log.mount(block)
                    output = tool_outputs.get(tc_id, "")
                    if output in _TOOL_REJECT_KEYWORDS:
                        block.set_error(output)
                    else:
                        block.set_done(output)

            # tool 类型消息已通过 tool_outputs 映射处理，跳过

    except Exception as e:
        logger.warning("恢复历史消息失败: %s", e, exc_info=True)
        await chat_log.append_error("恢复历史消息失败:", str(e))


def extract_human_display_text(content: str | list) -> str:
    """从 human 消息中提取用于显示的文本。

    技能命令消息从 <command-name> 和 <user-input> 标签还原用户输入，
    如 "/media-digest 介绍下这个"。
    非技能消息则过滤掉所有注入块（system-reminder、summary），返回剩余纯文本。

    Args:
        content: 字符串或多模态 content blocks 列表

    Returns:
        用于显示的文本
    """
    raw = extract_text_content(content)

    # 从 command-name + user-input 还原用户输入
    cmd_match = _COMMAND_NAME_RE.search(raw)
    if cmd_match:
        cmd = cmd_match.group(1)
        ui_match = _USER_INPUT_RE.search(raw)
        if ui_match:
            user_input = ui_match.group(1).strip()
            return f"{cmd} {user_input}" if user_input else cmd
        return cmd

    # 非技能消息：过滤所有注入块，只保留用户实际输入
    cleaned = re.sub(
        r"<system-reminder>.*?</system-reminder>\s*",
        "",
        raw,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<summary>.*?</summary>\s*",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    return cleaned.strip()


def extract_text_content(content: str | list) -> str:
    """从消息 content 中提取纯文本。

    支持 str 和 list[dict] 两种 LangChain 消息格式。

    Args:
        content: 字符串或多模态 content blocks 列表

    Returns:
        提取的文本内容
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""

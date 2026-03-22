"""历史消息恢复 — 从 LangGraph checkpoint 读取消息并渲染到 ChatLog。

将 checkpoint 消息转换为 RenderItem 列表，交由 WidgetAssembler 统一组装，
与实时渲染共享相同的分组和挂载逻辑。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from lumi.tui.render_items import (
    AgentEndItem,
    AgentStartItem,
    AssistantTextItem,
    FlushItem,
    RenderItem,
    ToolEndItem,
    ToolStartItem,
    UserItem,
)
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from lumi.tui.widgets.chat_log import ChatLog

# ── 常量 ──

# 恢复历史消息的最大数量（限制 DOM 节点数）
_MAX_RESTORE_MESSAGES: Final[int] = 60

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

        # 限制恢复数量，只保留最近的消息
        total_count = len(messages)
        truncated = total_count > _MAX_RESTORE_MESSAGES
        if truncated:
            messages = messages[-_MAX_RESTORE_MESSAGES:]

        # 预先收集所有 tool 消息的输出，key 为 tool_call_id
        tool_outputs: dict[str, str] = {}
        for msg in messages:
            msg_type = getattr(msg, "type", None)
            if msg_type == "tool":
                tc_id = getattr(msg, "tool_call_id", None)
                content = getattr(msg, "content", "")
                if tc_id:
                    tool_outputs[tc_id] = extract_text_content(content)

        if truncated:
            await chat_log.append_hint(
                "● ",
                f"仅显示最近 {_MAX_RESTORE_MESSAGES} 条消息（共 {total_count} 条）",
            )

        # 转换为 RenderItem 列表，交由 WidgetAssembler 统一组装
        items = _messages_to_items(messages, tool_outputs)
        assembler = WidgetAssembler(chat_log)
        for item in items:
            await assembler.apply_item(item)

        # AgentGroup 的 add_agent 通过 call_after_refresh 延迟挂载 _AgentLine，
        # 需要额外刷新一次确保显示最终状态。
        if assembler.agent_group is not None:
            ag = assembler.agent_group
            ag.call_after_refresh(ag._refresh_header)
            ag.call_after_refresh(ag._refresh_lines)

    except Exception as e:
        logger.warning("恢复历史消息失败: %s", e, exc_info=True)
        await chat_log.append_error("恢复历史消息失败:", str(e))


def _messages_to_items(
    messages: list, tool_outputs: dict[str, str]
) -> list[RenderItem]:
    """将 LangGraph 消息列表转换为 RenderItem 列表（纯函数）。

    Args:
        messages: LangGraph 消息列表
        tool_outputs: tool_call_id → 输出文本的映射
    """
    items: list[RenderItem] = []

    for msg in messages:
        msg_type = getattr(msg, "type", None) or (
            msg.get("type") if isinstance(msg, dict) else None
        )
        content = getattr(msg, "content", None) or (
            msg.get("content", "") if isinstance(msg, dict) else ""
        )

        if msg_type == "human":
            items.append(FlushItem())
            display = extract_human_display_text(content)
            if display:
                items.append(UserItem(text=display))

        elif msg_type == "ai":
            # 文本内容
            text = extract_text_content(content)
            if text:
                items.append(FlushItem())
                items.append(AssistantTextItem(text=text, finalized=True))

            # tool_calls
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                tc_id = tc.get("id", "")
                output = tool_outputs.get(tc_id, "")
                is_error = output in _TOOL_REJECT_KEYWORDS

                if name == "agent":
                    run_id = f"restore-{tc_id}" if tc_id else f"restore-{id(tc)}"
                    items.append(
                        AgentStartItem(
                            run_id=run_id,
                            agent_name=args.get("name", "agent"),
                            prompt=args.get("prompt", ""),
                        )
                    )
                    items.append(
                        AgentEndItem(
                            run_id=run_id,
                            output=output,
                            is_error=is_error,
                        )
                    )
                else:
                    key = tc_id or f"restore-{name}-{id(tc)}"
                    items.append(ToolStartItem(key=key, name=name, args=args))
                    items.append(
                        ToolEndItem(
                            key=key,
                            name=name,
                            output=output,
                            is_error=is_error,
                        )
                    )

        # tool 类型消息已通过 tool_outputs 映射处理，跳过

    items.append(FlushItem())
    return items


def extract_human_display_text(content: str | list) -> str:
    """从 human 消息中提取用于显示的文本。

    技能命令消息从 <command-name> 和 <user-input> 标签还原用户输入，
    如 "/media-digest 介绍下这个"。
    非技能消息则过滤掉所有注入块（system-reminder、summary），返回剩余纯文本。
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

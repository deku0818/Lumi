"""历史消息恢复 — 从 LangGraph checkpoint 读取消息并渲染到 ChatLog。

恢复时限制最大消息数量，避免一次性挂载过多 widget 导致界面卡顿。
连续工具调用合并为 ToolGroup 折叠摘要，agent 工具合并为 AgentGroup 轻量摘要，
与实时渲染保持一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from lumi.tui.widgets.agent_group import AgentGroup
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.tool_group import ToolGroup, should_exclude_from_group
from lumi.tui.widgets.user_message import UserMessage
from lumi.utils.logger import logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from lumi.tui.widgets.chat_log import ChatLog

from textual.widget import Widget

# ── 正则和常量 ──

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


@dataclass
class _ToolEntry:
    """恢复时暂存的单个常规工具调用信息。"""

    block: ToolBlock
    name: str
    args: dict


@dataclass
class _AgentEntry:
    """恢复时暂存的单个 agent 工具调用信息。"""

    agent_name: str
    prompt: str
    output: str
    is_error: bool


@dataclass
class _ToolGroupPlan:
    """恢复时的 ToolGroup 挂载计划。"""

    entries: list[_ToolEntry] = field(default_factory=list)


@dataclass
class _AgentGroupPlan:
    """恢复时的 AgentGroup 挂载计划。"""

    entries: list[_AgentEntry] = field(default_factory=list)


async def restore_messages(
    graph: CompiledStateGraph | None,
    chat_log: ChatLog,
    thread_id: str,
    checkpoint_id: str = "",
) -> None:
    """从 checkpoint 恢复历史消息并渲染到 ChatLog。

    处理 human、ai（含 tool_calls）和 tool 类型消息。
    先收集所有 tool 消息的输出，再按顺序渲染，确保 ToolBlock 能匹配到输出。
    超过 _MAX_RESTORE_MESSAGES 条时只恢复最近的消息，避免 DOM 过大导致卡顿。

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

        # 构建挂载计划：
        # - 连续常规工具调用合并为 ToolGroup
        # - 连续 agent 工具调用合并为 AgentGroup
        # - 文本 / 用户消息打断分组
        plan: list[Widget | _ToolGroupPlan | _AgentGroupPlan] = []
        pending_tools: list[_ToolEntry] = []
        pending_agents: list[_AgentEntry] = []

        def _flush_tools() -> None:
            """将暂存的常规工具调用刷入计划。"""
            nonlocal pending_tools
            if not pending_tools:
                return
            if len(pending_tools) == 1:
                plan.append(pending_tools[0].block)
            else:
                plan.append(_ToolGroupPlan(entries=list(pending_tools)))
            pending_tools = []

        def _flush_agents() -> None:
            """将暂存的 agent 工具调用刷入计划。"""
            nonlocal pending_agents
            if not pending_agents:
                return
            plan.append(_AgentGroupPlan(entries=list(pending_agents)))
            pending_agents = []

        def _flush_all() -> None:
            _flush_tools()
            _flush_agents()

        for msg in messages:
            msg_type = getattr(msg, "type", None) or (
                msg.get("type") if isinstance(msg, dict) else None
            )
            content = getattr(msg, "content", None) or (
                msg.get("content", "") if isinstance(msg, dict) else ""
            )

            if msg_type == "human":
                _flush_all()
                display = extract_human_display_text(content)
                if display:
                    plan.append(UserMessage(display))

            elif msg_type == "ai":
                # 渲染文本内容
                text = extract_text_content(content)
                if text:
                    _flush_all()
                    assistant_msg = AssistantMessage()
                    assistant_msg.append_token(text)
                    plan.append(assistant_msg)

                # 渲染 tool_calls
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    name = tc.get("name", "unknown")
                    args = tc.get("args", {})
                    tc_id = tc.get("id", "")
                    output = tool_outputs.get(tc_id, "")
                    is_error = output in _TOOL_REJECT_KEYWORDS

                    if name == "agent":
                        # agent 工具 → AgentGroup
                        _flush_tools()
                        agent_name = args.get("name", "agent")
                        prompt = args.get("prompt", "")
                        pending_agents.append(
                            _AgentEntry(
                                agent_name=agent_name,
                                prompt=prompt,
                                output=output,
                                is_error=is_error,
                            )
                        )
                    elif should_exclude_from_group(name, False):
                        # 排除合并的工具（如 ask）→ 独立挂载
                        _flush_all()
                        block = ToolBlock(name, args)
                        if is_error:
                            block.set_error(output)
                        else:
                            block.set_done(output)
                        plan.append(block)
                    else:
                        # 常规工具 → ToolGroup
                        _flush_agents()
                        block = ToolBlock(name, args)
                        if is_error:
                            block.set_error(output)
                        else:
                            block.set_done(output)
                        pending_tools.append(
                            _ToolEntry(block=block, name=name, args=args)
                        )

            # tool 类型消息已通过 tool_outputs 映射处理，跳过

        _flush_all()

        # 执行挂载计划
        for item in plan:
            if isinstance(item, _ToolGroupPlan):
                group = ToolGroup()
                await chat_log.mount(group)
                for entry in item.entries:
                    await group.add_block(entry.block, entry.name, entry.args)
                    group.notify_block_done(entry.block)
            elif isinstance(item, _AgentGroupPlan):
                ag = AgentGroup()
                await chat_log.mount(ag)
                for i, entry in enumerate(item.entries):
                    run_id = f"restore-agent-{id(ag)}-{i}"
                    ag.add_agent(run_id, entry.agent_name, entry.prompt)
                    if entry.is_error:
                        ag.finish_agent_error(run_id, entry.output)
                    else:
                        ag.finish_agent(run_id, entry.output)
                # add_agent 通过 call_after_refresh 延迟挂载 _AgentLine，
                # 此时 finish_agent 已设置 entry 状态但 line 未渲染。
                # 等 line 挂载后再刷新一次确保显示最终状态。
                ag.call_after_refresh(ag._refresh_header)
                ag.call_after_refresh(ag._refresh_lines)
            else:
                await chat_log.mount(item)
                if isinstance(item, AssistantMessage):
                    item.finalize()

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

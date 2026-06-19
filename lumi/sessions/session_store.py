"""会话存储 - 从 checkpoint 查询历史会话列表

通过 LangGraph 的 graph.get_state() API 获取每个 thread 的 StateSnapshot，
提取首条用户消息、created_at 等信息。

底层使用轻量 SQL 查询获取 thread_id 列表（避免全量反序列化），
再逐个调用 get_state 获取完整快照。

workspace 隔离通过 RunnableConfig 的 metadata 字段实现，
checkpointer 在 SQL 层按 metadata.workspace_dir 过滤。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lumi.sessions.message_visibility import should_show_human_message
from lumi.sessions.text_cleaning import extract_display_text
from lumi.utils.logger import logger
from lumi.utils.thread_id import CRON_THREAD_PREFIX

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


@dataclass(frozen=True)
class SessionSummary:
    """会话摘要（不可变）

    Attributes:
        thread_id: 会话线程 ID
        first_message: 首条用户消息摘要
        created_at: 最后 checkpoint 创建时间（UTC）
        message_count: 消息数量
    """

    thread_id: str
    first_message: str
    created_at: datetime
    message_count: int

    @property
    def display_time(self) -> str:
        """格式化显示时间（相对时间）"""
        now = datetime.now(tz=UTC)
        # created_at 可能是 naive datetime，统一处理
        ts = (
            self.created_at
            if self.created_at.tzinfo
            else self.created_at.replace(tzinfo=UTC)
        )
        delta = now - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "just now"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        days = delta.days
        if days == 1:
            return "yesterday"
        if days < 30:
            return f"{days} days ago"
        return self.created_at.strftime("%Y-%m-%d")


def _extract_first_human_message(messages: list) -> str:
    """从消息列表中提取首条用户消息

    支持 LangChain Message 对象和字典两种格式。
    自动跳过 system-reminder 等注入块，提取用户实际输入。

    Args:
        messages: StateSnapshot.values 中的 messages 列表

    Returns:
        首条用户消息文本（截断至 100 字符），提取失败返回空字符串
    """
    for msg in messages:
        # LangChain Message 对象
        if hasattr(msg, "type") and msg.type == "human":
            content = msg.content
        # 字典格式
        elif isinstance(msg, dict) and msg.get("type") == "human":
            content = msg.get("content", "")
        else:
            continue

        if not should_show_human_message(msg):
            continue

        if isinstance(content, str):
            return extract_display_text(content)[:100]
        # 多模态消息：遍历所有 text block，跳过 system-reminder
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    cleaned = extract_display_text(block.get("text", ""))
                    if cleaned:
                        return cleaned[:100]
    return ""


async def _get_thread_ids(
    graph: CompiledStateGraph,
    *,
    filter: dict[str, Any] | None = None,  # noqa: A002
) -> list[str]:
    """从 checkpointer 获取所有 thread_id 列表

    使用 checkpointer 的 alist 接口获取所有 checkpoint，
    按 checkpoint_id 降序排列（最新优先），去重提取 thread_id。

    Args:
        graph: 已编译的 LangGraph 状态图
        filter: metadata 过滤条件，传递给 checkpointer.alist()

    Returns:
        按最近活跃时间降序排列的 thread_id 列表
    """
    checkpointer = graph.checkpointer
    if checkpointer is None:
        return []

    seen: set[str] = set()
    thread_ids: list[str] = []

    # alist(config=None) 返回所有 checkpoint，按 checkpoint_id DESC
    # 只取每个 thread 的第一条（最新的）
    async for cp_tuple in checkpointer.alist(None, filter=filter):
        tid = cp_tuple.config["configurable"]["thread_id"]
        ns = cp_tuple.config["configurable"].get("checkpoint_ns", "")
        # 只取根命名空间的 checkpoint
        if ns != "":
            continue
        if tid not in seen:
            seen.add(tid)
            thread_ids.append(tid)

    return thread_ids


async def list_sessions(
    graph: CompiledStateGraph,
    *,
    current_thread_id: str = "",
    workspace: str = "",
    limit: int = 50,
) -> list[SessionSummary]:
    """查询所有历史会话摘要

    通过 graph.get_state() 获取每个 thread 的 StateSnapshot，
    提取首条用户消息和 created_at 时间戳。

    Args:
        graph: 已编译的 LangGraph 状态图（需要带 checkpointer）
        current_thread_id: 当前会话 thread_id，将从结果中排除
        workspace: 按工作目录过滤，空字符串表示不过滤
        limit: 最大返回数量

    Returns:
        按 created_at 降序排列的会话摘要列表
    """
    if graph.checkpointer is None:
        return []

    metadata_filter = {"workspace_dir": workspace} if workspace else None
    thread_ids = await _get_thread_ids(graph, filter=metadata_filter)
    # cron 执行会话不进会话列表（即使续聊后带上 workspace 元数据也不"转正"），
    # 只能从定时任务详情的执行记录进入
    candidates = [
        tid
        for tid in thread_ids
        if tid != current_thread_id and not tid.startswith(f"{CRON_THREAD_PREFIX}-")
    ]

    # 分批并发加载 state：串行 aget_state 在会话多时是侧栏刷新的延迟瓶颈；
    # 分批（而非全量 gather）保留 limit 早停，不为远超 limit 的旧会话买单
    sessions: list[SessionSummary] = []
    batch_size = 25
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        snapshots = await asyncio.gather(
            *(graph.aget_state({"configurable": {"thread_id": tid}}) for tid in batch),
            return_exceptions=True,
        )
        for tid, snapshot in zip(batch, snapshots):
            if isinstance(snapshot, BaseException):
                logger.warning("获取会话 %s 状态失败: %s", tid, snapshot)
                continue
            if not snapshot or not snapshot.values:
                continue
            messages = snapshot.values.get("messages", [])
            if not messages:
                continue
            first_msg = _extract_first_human_message(messages)
            if not first_msg:
                continue

            sessions.append(
                SessionSummary(
                    thread_id=tid,
                    first_message=first_msg,
                    # StateSnapshot.created_at 是 ISO 8601 字符串
                    created_at=_parse_created_at(snapshot.created_at),
                    message_count=len(messages),
                )
            )
            if len(sessions) >= limit:
                return sessions

    return sessions


def _parse_created_at(created_at: str | None) -> datetime:
    """解析 StateSnapshot.created_at 时间戳

    Args:
        created_at: ISO 8601 格式时间字符串

    Returns:
        解析后的 datetime，失败返回当前 UTC 时间
    """
    if created_at:
        try:
            return datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            pass
    return datetime.now(tz=UTC)

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

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite.utils import search_where

from lumi.sessions.message_text import visible_user_text
from lumi.sessions.message_visibility import (
    is_human_message,
    should_show_human_message,
)
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
        workspace_dir: 会话所属项目（工作目录），来自 checkpoint 元数据
    """

    thread_id: str
    first_message: str
    created_at: datetime
    message_count: int
    workspace_dir: str = ""

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
        if not (is_human_message(msg) and should_show_human_message(msg)):
            continue
        cleaned = visible_user_text(msg)
        if cleaned:
            return cleaned[:100]
    return ""


async def _get_thread_ids(
    graph: CompiledStateGraph,
    *,
    filter: dict[str, Any] | None = None,  # noqa: A002
) -> list[tuple[str, str]]:
    """从 checkpointer 获取所有 (thread_id, latest_checkpoint_id)

    sqlite / postgres 直接用轻量 SQL 取每个 thread 的最新 checkpoint_id——
    alist 会把每一行 checkpoint blob 完整反序列化，库大时（GB 级）
    一次列表要数十秒。checkpoint_id 用于缓存失效判断——
    内容未变则 id 不变，可跳过完整反序列化。

    Args:
        graph: 已编译的 LangGraph 状态图
        filter: metadata 过滤条件（形如 {"workspace_dir": ...}）

    Returns:
        按最近活跃时间降序排列的 (thread_id, checkpoint_id) 列表
    """
    checkpointer = graph.checkpointer
    if checkpointer is None:
        return []

    # 两条快路径同构：先在覆盖索引上分组取各 thread 最新 checkpoint_id（不碰行数据，
    # 尤其不碰 GB 级 checkpoint blob），再回表只对每 thread 一行探 metadata 过滤。
    # 语义注：过滤从"最新一条匹配的"变为"最新一条、且它匹配"——workspace_dir 随
    # thread 恒定，二者等价。metadata 谓词复用 langgraph 自家构建器，与 alist 同源。
    grouped_join = (
        "SELECT c.thread_id, c.checkpoint_id FROM"
        " (SELECT thread_id, MAX(checkpoint_id) AS checkpoint_id FROM checkpoints"
        "  WHERE checkpoint_ns = '' GROUP BY thread_id) AS m"
        " JOIN checkpoints AS c ON c.thread_id = m.thread_id"
        " AND c.checkpoint_id = m.checkpoint_id AND c.checkpoint_ns = ''"
    )

    if isinstance(checkpointer, AsyncSqliteSaver):
        where, params = search_where(None, filter)
        sql = f"{grouped_join} {where} ORDER BY c.checkpoint_id DESC"
        async with checkpointer.lock, checkpointer.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    if isinstance(checkpointer, AsyncPostgresSaver):
        where, pg_params = checkpointer._search_where(None, filter)
        sql = f"{grouped_join} {where} ORDER BY c.checkpoint_id DESC"
        async with checkpointer._cursor() as cur:
            await cur.execute(sql, pg_params)
            rows = await cur.fetchall()
        return [(row["thread_id"], row["checkpoint_id"]) for row in rows]

    # InMemorySaver 等其余类型：alist 在内存中遍历，无反序列化开销。
    # alist(config=None) 返回所有 checkpoint，按 checkpoint_id DESC，
    # 只取每个 thread 的第一条（最新的）
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    async for cp_tuple in checkpointer.alist(None, filter=filter):
        cfg = cp_tuple.config["configurable"]
        tid = cfg["thread_id"]
        # 只取根命名空间的 checkpoint
        if cfg.get("checkpoint_ns", "") != "":
            continue
        if tid not in seen:
            seen.add(tid)
            pairs.append((tid, cfg.get("checkpoint_id", "")))

    return pairs


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
    pairs = await _get_thread_ids(graph, filter=metadata_filter)
    # cron 执行会话不进会话列表（即使续聊后带上 workspace 元数据也不"转正"），
    # 只能从定时任务详情的执行记录进入
    candidates = [
        (tid, cid)
        for tid, cid in pairs
        if tid != current_thread_id and not tid.startswith(f"{CRON_THREAD_PREFIX}-")
    ]

    # 分批并发加载 state：串行 aget_state 在会话多时是侧栏刷新的延迟瓶颈；
    # 分批（而非全量 gather）保留 limit 早停，不为远超 limit 的旧会话买单。
    # 缓存命中（checkpoint_id 未变）的会话直接复用，跳过完整反序列化——
    # 删除/置顶/重命名后的刷新几乎全部命中，是侧栏卡顿的主要来源。
    sessions: list[SessionSummary] = []
    batch_size = 25
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        miss = [(tid, cid) for tid, cid in batch if _cache_get(tid, cid) is None]
        if miss:
            snapshots = await asyncio.gather(
                *(
                    graph.aget_state({"configurable": {"thread_id": tid}})
                    for tid, _ in miss
                ),
                return_exceptions=True,
            )
            for (tid, cid), snapshot in zip(miss, snapshots):
                if isinstance(snapshot, BaseException):
                    logger.warning("获取会话 %s 状态失败: %s", tid, snapshot)
                    continue
                summary = _summary_from_snapshot(tid, snapshot)
                if summary is not None:
                    _summary_cache[tid] = (cid, summary)

        for tid, cid in batch:
            cached = _cache_get(tid, cid)
            if cached is None:
                continue
            sessions.append(cached)
            if len(sessions) >= limit:
                return sessions

    return sessions


# thread_id -> (checkpoint_id, summary)。按 checkpoint_id 失效：
# 会话内容变化必产生新 checkpoint_id，id 一致即可安全复用。
# 删除的会话不再出现在 candidates，残留条目永不命中（数量有界，不主动清理）。
_summary_cache: dict[str, tuple[str, SessionSummary]] = {}


def _cache_get(thread_id: str, checkpoint_id: str) -> SessionSummary | None:
    """缓存命中（checkpoint_id 一致）返回 summary，否则 None"""
    entry = _summary_cache.get(thread_id)
    if entry is not None and entry[0] == checkpoint_id and checkpoint_id:
        return entry[1]
    return None


def _summary_from_snapshot(thread_id: str, snapshot: Any) -> SessionSummary | None:
    """从 StateSnapshot 构造 SessionSummary；无任何消息时返回 None。

    取不到首条用户消息**不再**丢弃会话（``first_message`` 留空）——压缩后的会话首条
    真实 human 可能已被并入摘要，此时仍是一个有内容的会话，标题由上层 meta（手动
    title / IM channel_title / 生成标题）兜住，不应从列表消失。
    """
    if not snapshot or not snapshot.values:
        return None
    messages = snapshot.values.get("messages", [])
    if not messages:
        return None
    return SessionSummary(
        thread_id=thread_id,
        first_message=_extract_first_human_message(messages),
        # StateSnapshot.created_at 是 ISO 8601 字符串
        created_at=_parse_created_at(snapshot.created_at),
        message_count=len(messages),
        # checkpoint 元数据里的项目目录；跨项目列表时供前端分组
        workspace_dir=(snapshot.metadata or {}).get("workspace_dir", ""),
    )


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

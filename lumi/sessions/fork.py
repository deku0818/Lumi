"""独立保存会话上下文和压缩历史；附件原地址共享，按会话登记引用。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from lumi.agents.core.meta_message import (
    HISTORY_ID_KEY,
    HISTORY_KEY,
    declared_file_paths,
    history_message_ids,
    should_show_human_message,
    strip_ctx_digest,
    visible_user_text,
)
from lumi.gateway.uploads import managed_uploads
from lumi.sessions import session_model
from lumi.sessions.history import HistoryReader, HistorySegment, compact_message
from lumi.sessions.session_meta import delete_meta, load_all, update_meta
from lumi.sessions.thread_runs import thread_runs
from lumi.utils.config.global_manager import GlobalConfigManager
from lumi.utils.json_sidecar import load_sidecar, update_sidecar

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

_fork_lock = asyncio.Lock()


class ForkSessionResult(TypedDict):
    thread_id: str
    title: str
    workspace_dir: str


def _sequence_path() -> Path:
    # 独立于会话元数据：删除原会话和全部分支后，也不会复用已分配的编号。
    return GlobalConfigManager.load().get_checkpoint_dir() / "session_forks.json"


def _allocate_title(root: str, base_title: str) -> str:
    path = _sequence_path()
    previous = load_sidecar(path).get(root, {})
    number = previous.get("sequence", 0) + 1
    title = previous.get("title") or base_title
    update_sidecar(path, root, sequence=number, title=title)
    return f"{title} · 分支 {number}"


def _copy_messages(
    messages: list[BaseMessage],
    aliases: dict[str, str],
    previous_id: str,
    previous_exclude: list[str],
) -> list[BaseMessage]:
    copied: list[BaseMessage] = []
    for message, identity in zip(messages, history_message_ids(messages)):
        clone = strip_ctx_digest(message.model_copy(deep=True))
        clone.additional_kwargs[HISTORY_ID_KEY] = aliases.get(identity, identity)
        copied.append(clone)
    # 连旧版无索引摘要也改为新会话自己的 checkpoint，绝不引用来源会话。
    if previous_id and (compact := compact_message(copied)):
        compact[0].additional_kwargs[HISTORY_KEY] = {
            "checkpoint_id": previous_id,
            "exclude": previous_exclude,
        }
    return copied


async def _write_history(
    graph: CompiledStateGraph,
    target: RunnableConfig,
    segments: list[HistorySegment],
) -> None:
    previous_id = ""
    previous_exclude: list[str] = []
    for messages, position in reversed(segments):
        aliases = dict(position.aliases)
        copied = _copy_messages(messages, aliases, previous_id, previous_exclude)
        saved = await graph.aupdate_state(
            target,
            {
                "messages": Overwrite(copied),
                "ptl_retry": False,
                "tool_cancelled": False,
                "todos": [],
            },
            as_node="OfflineFlush",
        )
        previous_id = saved["configurable"]["checkpoint_id"]
        previous_exclude = [
            aliases.get(identity, identity) for identity in position.exclude
        ]


def _base_title(metadata: dict[str, Any], messages: list[BaseMessage]) -> str:
    first_message = next(
        (
            visible_user_text(message)
            for message in messages
            if message.type == "human" and should_show_human_message(message)
        ),
        "新会话",
    )
    return (
        metadata.get("title")
        or metadata.get("channel_title")
        or metadata.get("auto_title")
        or first_message[:100]
    )


async def fork_session(
    graph: CompiledStateGraph,
    thread_id: str,
    checkpoint_id: str,
    message_id: str,
    request_id: str,
) -> ForkSessionResult:
    """同一 request_id 可重试；重复点击使用新 id，生成不同的普通会话。"""
    try:
        target_id = f"t-{UUID(request_id).hex}"
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("无效的分支请求 ID") from exc
    if not thread_id or not checkpoint_id or not message_id:
        raise ValueError("缺少分支来源")
    source = {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "message_id": message_id,
    }
    target: RunnableConfig = {"configurable": {"thread_id": target_id}}
    async with _fork_lock:
        existing = await graph.aget_state(target)
        if existing.values:
            target_metadata = load_all().get(target_id, {})
            if target_metadata.get("fork_source") != source:
                raise ValueError("分支请求 ID 已被使用")
            if not target_metadata.get("fork_pending"):
                return {
                    "thread_id": target_id,
                    "title": target_metadata.get("title", ""),
                    "workspace_dir": (existing.metadata or {}).get("workspace_dir", ""),
                }
        async with (
            thread_runs.lock_for(thread_id).idle(),
            thread_runs.lock_for(target_id).idle(),
        ):
            if thread_runs.is_deleting(thread_id) or thread_runs.is_deleting(target_id):
                raise ValueError("会话正在删除，请稍后重试")
            latest = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            if not latest.values:
                raise ValueError("原会话已不存在")
            segments = await HistoryReader().fork_segments(
                graph, thread_id, checkpoint_id, message_id
            )
            workspace = (latest.metadata or {}).get("workspace_dir", "")
            target["metadata"] = {"workspace_dir": workspace}
            shared_uploads = managed_uploads(
                path
                for messages, _ in segments
                for message in messages
                for path in declared_file_paths(message)
            )
            source_metadata = load_all().get(thread_id, {})
            if shared_uploads:
                update_meta(
                    thread_id,
                    shared_uploads=sorted(
                        set(source_metadata.get("shared_uploads", [])) | shared_uploads
                    ),
                )
            try:
                update_meta(
                    target_id,
                    fork_source=source,
                    fork_pending=True,
                    shared_uploads=sorted(shared_uploads) or None,
                )
                await _write_history(graph, target, segments)
                source_metadata = load_all().get(thread_id, {})
                root = source_metadata.get("fork_root", thread_id)
                base = _base_title(source_metadata, segments[-1][0])
                title = _allocate_title(root, base)
                model = session_model.resolve(thread_id)
                update_meta(
                    target_id,
                    title=title,
                    fork_root=root,
                    fork_source=source,
                    fork_pending=None,
                    model=model.model,
                    model_provider=model.provider,
                    effort=model.effort,
                )
            except BaseException:
                await graph.checkpointer.adelete_thread(target_id)
                delete_meta(target_id)
                raise
    return {"thread_id": target_id, "title": title, "workspace_dir": workspace}

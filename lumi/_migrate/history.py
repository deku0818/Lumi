"""临时迁移：为旧压缩记录补齐历史索引，仅由 lumi migrate 导入。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage
from langgraph.types import Overwrite

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from lumi.agents.core.meta_message import (
    CTX_DIGEST_KEY,
    HISTORY_ID_KEY,
    extract_text_content,
    should_show_human_message,
)


def history_message_ids(messages: list[BaseMessage]) -> list[str]:
    counts: dict[str, int] = {}
    result = []
    for message in messages:
        identity = message.additional_kwargs.get(HISTORY_ID_KEY) or message.id
        if not identity:
            digest = hashlib.sha256(message.model_dump_json().encode()).hexdigest()
            index = counts.get(digest, 0)
            counts[digest] = index + 1
            identity = f"legacy:{digest}:{index}"
        result.append(identity)
    return result


@dataclass(frozen=True)
class HistoryLink:
    checkpoint_id: str
    exclude: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]


def compact_message(messages: list[BaseMessage]) -> tuple[BaseMessage, str] | None:
    for index, msg in enumerate(messages):
        if (
            msg.type in ("human", "system")
            and (msg.type == "system" or not should_show_human_message(msg))
            and extract_text_content(msg.content).startswith("<compact>")
        ):
            identity = msg.additional_kwargs.get(HISTORY_ID_KEY) or msg.id
            if not identity:
                identity = history_message_ids(messages[: index + 1])[-1]
            return msg, identity
    return None


def _reattached_value(msg: BaseMessage) -> dict:
    value = msg.model_dump(exclude={"id"})
    value["additional_kwargs"].pop(CTX_DIGEST_KEY, None)
    value["additional_kwargs"].pop(HISTORY_ID_KEY, None)
    return value


def _history_link(
    checkpoint_id: str, before: list[BaseMessage], retained: list[BaseMessage]
) -> HistoryLink:
    """旧压缩换了 id：按重挂原文的完整结构和原有顺序匹配，保留同文不同轮。"""
    identities = history_message_ids(before)
    position = len(before)
    excluded: list[str] = []
    aliases: list[tuple[str, str]] = []
    for msg, retained_id in reversed(
        list(zip(retained, history_message_ids(retained)))
    ):
        value = _reattached_value(msg)
        match = next(
            (
                i
                for i in range(position - 1, -1, -1)
                if _reattached_value(before[i]) == value
            ),
            None,
        )
        if match is None:
            raise ValueError("无法匹配旧压缩记录的保留消息")
        excluded.append(identities[match])
        aliases.append((identities[match], retained_id))
        position = match
    return HistoryLink(checkpoint_id, tuple(excluded), tuple(aliases))


async def find_history_link(
    graph: CompiledStateGraph, thread_id: str, checkpoint_id: str, compact_id: str
) -> HistoryLink | None:
    saver = graph.checkpointer
    saved = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
    )
    seen: set[str] = set()
    post_messages: list[BaseMessage] | None = None
    while saved:
        checkpoint_id = saved.config["configurable"]["checkpoint_id"]
        if checkpoint_id in seen:
            return None
        seen.add(checkpoint_id)
        for _, channel, value in saved.pending_writes or []:
            if channel != "messages":
                continue
            messages = value.value if isinstance(value, Overwrite) else value
            if not isinstance(messages, list):
                continue
            compact = compact_message(messages)
            if compact and compact[1] == compact_id:
                before = await graph.aget_state(saved.config)
                retained = messages[messages.index(compact[0]) + 1 :]
                return _history_link(
                    checkpoint_id,
                    before.values.get("messages", []),
                    retained,
                )
        # add_messages 时代的 checkpoint 自带整份消息，不一定留有压缩写入。
        stored = saved.checkpoint.get("channel_values", {}).get("messages")
        stored = getattr(stored, "value", stored)
        if isinstance(stored, list):
            compact = compact_message(stored)
            same_carrier = compact and compact[1] == compact_id
            if compact and not compact[0].id and post_messages is not None:
                post_compact = compact_message(post_messages)[0]
                stored_tail = stored[stored.index(compact[0]) + 1 :]
                post_tail = post_messages[post_messages.index(post_compact) + 1 :]
                # 旧 Overwrite 的 carrier 可能在下一次 add_messages 时才获得 id。
                # 同结构 carrier + 保留消息的 id 前缀一致，说明仍是同一次压缩。
                same_carrier = same_carrier or (
                    _reattached_value(compact[0]) == _reattached_value(post_compact)
                    and history_message_ids(stored_tail)
                    == history_message_ids(post_tail)[: len(stored_tail)]
                )
            if same_carrier:
                post_messages = stored
            elif post_messages is not None:
                after_compact = compact_message(post_messages)[0]
                retained = post_messages[post_messages.index(after_compact) + 1 :]
                return _history_link(checkpoint_id, stored, retained)
        if not saved.parent_config:
            break
        saved = await saver.aget_tuple(saved.parent_config)
    return None

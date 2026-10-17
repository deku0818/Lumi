"""按压缩边界读取 checkpoint 原文；只缓存一段展示数据，不复制持久消息。"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage
from langgraph.types import Overwrite, StateSnapshot

from lumi.agents.core.meta_message import (
    CTX_DIGEST_KEY,
    HISTORY_ID_KEY,
    HISTORY_KEY,
    extract_text_content,
    history_message_ids,
    should_show_human_message,
)
from lumi.agents.core.node_helpers.messages import dangling_tool_calls
from lumi.sessions.thread_runs import ThreadRunLock, thread_runs

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


def can_fork_message(message: BaseMessage) -> bool:
    """仅完整的助手正文可作为分支点，工具过程和中断的半截正文不可。"""
    return (
        message.type == "ai"
        and bool(extract_text_content(message.content))
        and not getattr(message, "tool_calls", None)
        and not message.additional_kwargs.get("lumi", {}).get("interrupted")
    )


@dataclass(frozen=True)
class _Cursor:
    thread_id: str
    checkpoint_id: str
    end: int | None = None
    exclude: tuple[str, ...] = ()
    archived: bool = False
    compact_id: str = ""
    aliases: tuple[tuple[str, str], ...] = ()

    def encode(self) -> str:
        return base64.urlsafe_b64encode(json.dumps(asdict(self)).encode()).decode()

    @classmethod
    def decode(cls, value: str, thread_id: str) -> _Cursor:
        try:
            if not isinstance(value, str) or len(value) > 65536:
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(value))
            if not isinstance(data, dict) or not isinstance(
                data.get("exclude", []), list
            ):
                raise ValueError
            cursor = cls(
                **{
                    **data,
                    "exclude": tuple(data.get("exclude", [])),
                    "aliases": tuple(tuple(pair) for pair in data.get("aliases", [])),
                }
            )
            if (
                cursor.thread_id != thread_id
                or not isinstance(cursor.checkpoint_id, str)
                or not cursor.checkpoint_id
                or (
                    cursor.end is not None
                    and (type(cursor.end) is not int or cursor.end < 0)
                )
                or not all(isinstance(identity, str) for identity in cursor.exclude)
                or type(cursor.archived) is not bool
                or not isinstance(cursor.compact_id, str)
                or not all(
                    len(pair) == 2 and all(isinstance(part, str) for part in pair)
                    for pair in cursor.aliases
                )
            ):
                raise ValueError
            return cursor
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("无效的历史分页游标") from exc


type HistorySegment = tuple[list[BaseMessage], _Cursor]


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


def _previous(
    thread_id: str, checkpoint_id: str, messages: list[BaseMessage]
) -> _Cursor | None:
    compact = compact_message(messages)
    if compact is None:
        return None
    msg, identity = compact
    link = msg.additional_kwargs.get(HISTORY_KEY)
    if link:
        return _Cursor(
            thread_id,
            link["checkpoint_id"],
            exclude=tuple(link["exclude"]),
            archived=True,
        )
    # 旧摘要没有索引。等用户真正翻到这里才沿祖先查找，不拖慢首次打开。
    return _Cursor(thread_id, checkpoint_id, archived=True, compact_id=identity)


def _reattached_value(msg: BaseMessage) -> dict:
    value = msg.model_dump(exclude={"id"})
    value["additional_kwargs"].pop(CTX_DIGEST_KEY, None)
    value["additional_kwargs"].pop(HISTORY_ID_KEY, None)
    return value


def _display_alias(identity: str, aliases: tuple[tuple[str, str], ...]) -> str:
    for original, reattached in aliases:
        if identity.startswith(f"{original}:"):
            return reattached + identity[len(original) :]
    return identity


def _legacy_exclusions(
    before: list[BaseMessage], retained: list[BaseMessage]
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
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
    return tuple(excluded), tuple(aliases)


class HistoryReader:
    def __init__(self) -> None:
        self._key: tuple | None = None
        self._items: list[dict] = []
        self._previous: _Cursor | None = None
        self._excluded: set[str] = set()
        self._run_lock: ThreadRunLock | None = None
        self._settled = False

    @staticmethod
    def _config(cursor: _Cursor) -> dict:
        return {
            "configurable": {
                "thread_id": cursor.thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": cursor.checkpoint_id,
            }
        }

    async def _legacy_cursor(
        self, graph: CompiledStateGraph, cursor: _Cursor
    ) -> _Cursor | None:
        saver = graph.checkpointer
        saved = await saver.aget_tuple(self._config(cursor))
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
                if compact and compact[1] == cursor.compact_id:
                    before = await graph.aget_state(saved.config)
                    retained = messages[messages.index(compact[0]) + 1 :]
                    return self._legacy_link(
                        cursor,
                        checkpoint_id,
                        before.values.get("messages", []),
                        retained,
                    )
            # add_messages 时代的 checkpoint 自带整份消息，不一定留有压缩写入。
            stored = saved.checkpoint.get("channel_values", {}).get("messages")
            stored = getattr(stored, "value", stored)
            if isinstance(stored, list):
                compact = compact_message(stored)
                same_carrier = compact and compact[1] == cursor.compact_id
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
                    return self._legacy_link(cursor, checkpoint_id, stored, retained)
            if not saved.parent_config:
                break
            saved = await saver.aget_tuple(saved.parent_config)
        return None

    @staticmethod
    def _legacy_link(
        cursor: _Cursor,
        checkpoint_id: str,
        before: list[BaseMessage],
        retained: list[BaseMessage],
    ) -> _Cursor:
        exclude, aliases = _legacy_exclusions(before, retained)
        previous_aliases = dict(cursor.aliases)
        mapping = {
            original: previous_aliases.get(reattached, reattached)
            for original, reattached in aliases
        }
        return _Cursor(
            cursor.thread_id,
            checkpoint_id,
            exclude=exclude,
            archived=True,
            aliases=tuple(mapping.items()),
        )

    async def page(
        self,
        graph: CompiledStateGraph,
        thread_id: str,
        render: Callable[[list], list[dict]],
        *,
        cursor: str | None = None,
        limit: int = 50,
        snapshot: StateSnapshot | None = None,
    ) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("历史分页大小须为 1～200")
        run_lock = thread_runs.lock_for(thread_id)
        revision = run_lock.history_revision
        unavailable = {"items": [], "next_cursor": None, "history_unavailable": True}
        if run_lock.deleting:
            return unavailable
        if cursor:
            position = _Cursor.decode(cursor, thread_id)
            if position.compact_id:
                position = await self._legacy_cursor(graph, position)
                if position is None:
                    return unavailable
        else:
            snapshot = snapshot or await graph.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
            checkpoint_id = (
                (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
            )
            position = _Cursor(thread_id, checkpoint_id or "")

        # 读取可以跨过删除的 await；旧结果不能被记到删除后的 revision 下。
        if run_lock.deleting or run_lock.history_revision != revision:
            return unavailable
        if not position.checkpoint_id:
            return {
                "items": render(snapshot.values.get("messages", [])),
                "next_cursor": None,
            }

        key = (
            id(graph.checkpointer),
            thread_id,
            revision,
            position.checkpoint_id,
            position.exclude,
            position.aliases,
        )
        # 最新状态会合入同一 checkpoint 的待写入结果；只复用前后均已静止的快照。
        settled = (
            snapshot is not None
            and not snapshot.next
            and not snapshot.tasks
            and not run_lock.locked()
        )
        refresh = snapshot is not None and not (settled and self._settled)
        if self._key != key or refresh:
            snap = snapshot or await graph.aget_state(self._config(position))
            if run_lock.deleting or run_lock.history_revision != revision:
                return unavailable
            if not snap.values:
                return unavailable
            messages = snap.values.get("messages", [])
            # 整段配对后才分页，ToolMessage 即使不在同一页，输出也完整。
            excluded = set(position.exclude)
            identities = history_message_ids(messages)
            normalized = [
                m
                if m.id
                else m.model_copy(
                    update={
                        "additional_kwargs": {
                            **m.additional_kwargs,
                            HISTORY_ID_KEY: identity,
                        }
                    }
                )
                for m, identity in zip(messages, identities)
            ]
            self._items = render(normalized)
            fork_ids = {
                identity
                for message, identity in zip(messages, identities)
                if can_fork_message(message)
            }
            for item in self._items:
                if item["kind"] == "assistant":
                    identity = item["id"].removesuffix(":assistant")
                    if identity in fork_ids:
                        item["fork_point"] = {
                            "message_id": identity,
                            "checkpoint_id": position.checkpoint_id,
                        }
            self._excluded = {
                it["id"]
                for it in render(
                    [
                        m
                        for m, identity in zip(normalized, identities)
                        if identity in excluded
                    ]
                )
            }
            self._previous = _previous(thread_id, position.checkpoint_id, messages)
            if self._previous:
                self._previous = replace(self._previous, aliases=position.aliases)
            self._key = key
            self._run_lock = run_lock
            self._settled = settled

        end = len(self._items) if position.end is None else position.end
        if position.end is None:
            while end and self._items[end - 1]["id"] in self._excluded:
                end -= 1
        if end > len(self._items):
            raise ValueError("历史分页位置超出记录范围")
        start = max(0, end - limit)
        items = [it for it in self._items[start:end] if it["id"] not in self._excluded]
        if position.archived:
            items = [
                {k: v for k, v in item.items() if k != "message_id"} for item in items
            ]
        next_position = replace(position, end=start) if start else self._previous
        result = {
            "items": items,
            "next_cursor": next_position.encode() if next_position else None,
        }
        if cursor:
            # 被重挂的消息虽然不重复返回正文，仍是页内的顺序锚点。
            result["order"] = [
                _display_alias(it["id"], position.aliases)
                for it in self._items[start:end]
            ]
        if cursor and end < len(self._items):
            result["insert_before"] = _display_alias(
                self._items[end]["id"], position.aliases
            )
        if not cursor:
            result["user_message_ids"] = {
                it["id"]: it["message_id"]
                for it in self._items
                if it["kind"] == "user" and it.get("message_id")
            }
        return result

    async def fork_segments(
        self,
        graph: CompiledStateGraph,
        thread_id: str,
        checkpoint_id: str,
        message_id: str,
    ) -> list[HistorySegment]:
        """分支上下文与其引用的压缩原文，按从新到旧排列，不读取无关超步。"""
        position = _Cursor(thread_id, checkpoint_id)
        snapshot = await graph.aget_state(self._config(position))
        messages = list(snapshot.values.get("messages", []))
        identities = history_message_ids(messages)
        if message_id not in identities:
            raise ValueError("分支消息不存在，请刷新会话")
        index = identities.index(message_id)
        if not can_fork_message(messages[index]):
            raise ValueError("只能从已完成的助手回复创建分支")
        # 被保尾重挂的回复可能落在一个已概括未来的摘要之后，恢复它压缩前的上下文。
        earlier, index = await message_checkpoint(
            graph, thread_id, messages, index, checkpoint_id=checkpoint_id
        )
        if earlier is not None:
            snapshot = earlier
            messages = list(snapshot.values["messages"])
            position = replace(
                position,
                checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
            )
        messages = messages[: index + 1]
        if dangling_tool_calls(messages):
            raise ValueError("该回复的工具调用尚未完成，无法创建分支")
        segments = [(messages, position)]
        seen = {position.checkpoint_id}
        while previous := _previous(thread_id, position.checkpoint_id, messages):
            previous = replace(previous, aliases=position.aliases)
            if previous.compact_id:
                previous = await self._legacy_cursor(graph, previous)
                if previous is None:
                    raise ValueError("压缩前记录已不可用，无法创建完整分支")
            if previous.checkpoint_id in seen:
                raise ValueError("历史索引循环，无法创建分支")
            seen.add(previous.checkpoint_id)
            snapshot = await graph.aget_state(self._config(previous))
            if not snapshot.values:
                raise ValueError("压缩前记录已不可用，无法创建完整分支")
            messages = list(snapshot.values.get("messages", []))
            segments.append((messages, previous))
            position = previous
        return segments


async def message_checkpoint(
    graph: CompiledStateGraph,
    thread_id: str,
    messages: list[BaseMessage],
    index: int,
    *,
    checkpoint_id: str = "",
) -> tuple[StateSnapshot | None, int]:
    """找到目标消息压缩前的最早状态，供 rewind / fork 排除旧未来。"""
    identity = history_message_ids(messages)[index]
    position = _previous(thread_id, checkpoint_id, messages)
    reader = HistoryReader()
    source: StateSnapshot | None = None
    seen: set[str] = set()
    while position:
        if not position.compact_id and identity not in position.exclude:
            break  # 目标是压缩后新增的消息，不需要恢复更早状态。
        if position.compact_id:
            if not position.checkpoint_id:
                current = await graph.aget_state(
                    {"configurable": {"thread_id": thread_id}}
                )
                position = replace(
                    position,
                    checkpoint_id=current.config["configurable"]["checkpoint_id"],
                )
            position = await reader._legacy_cursor(graph, position)
            if position is None:
                raise ValueError("压缩前记录已不可用，无法安全回退")
        if position.checkpoint_id in seen:
            raise ValueError("历史索引循环，无法安全回退")
        seen.add(position.checkpoint_id)
        snapshot = await graph.aget_state(reader._config(position))
        if not snapshot.values:
            raise ValueError("压缩前记录已不可用，无法安全回退")
        earlier = snapshot.values.get("messages", [])
        identities = history_message_ids(earlier)
        aliases = dict(position.aliases)
        found = next(
            (
                i
                for i, key in enumerate(identities)
                if key == identity or aliases.get(key) == identity
            ),
            None,
        )
        if found is None:
            break
        source, index = snapshot, found
        identity = identities[index]
        position = _previous(thread_id, position.checkpoint_id, earlier)
    return source, index

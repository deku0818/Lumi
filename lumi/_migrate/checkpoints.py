"""临时迁移 checkpoint 与本地关联数据库；正常运行不导入本模块。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import aiosqlite
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver, SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Send
from pydantic import BaseModel

from lumi._migrate.history import (
    compact_message,
    find_history_link,
    history_message_ids,
)
from lumi.agents.core.meta_message import HISTORY_ID_KEY, HISTORY_KEY
from lumi.models.reasoning import ORIGIN

if TYPE_CHECKING:
    from lumi.utils.config.models import AgentsConfig

Query = Callable[..., Awaitable[Any]]

_NODES = {"HumanApproval": "ToolExecutor", "AutoClassify": "ToolExecutor"}
_IDS = {
    "thread_id",
    "agent_id",
    "parent_id",
    "parent_thread_id",
    "source_thread",
    "root_thread",
    "fork_root",
}


def rename_channel(name: str) -> str:
    if name.startswith("branch:to:"):
        return "branch:to:" + _NODES.get(name[10:], name[10:])
    return _NODES.get(name, name)


@contextmanager
def migration_serde():
    """旧 Todo 类型只在显式迁移期间注册，不要求主程序保留旧模块。"""
    name = "lumi.agents.tools.providers.todo"
    module = ModuleType(name)

    class Todo(BaseModel):
        content: str
        status: str

    Todo.__module__ = name
    module.Todo = Todo
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        serde = JsonPlusSerializer(allowed_msgpack_modules=[(name, "Todo")])
        key = os.environ.get("LUMI_CHECKPOINT_AES_KEY")
        if key:
            serde = EncryptedSerializer.from_pycryptodome_aes(
                serde=serde, key=key.encode()
            )
        yield serde
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def rename_thread(thread: str, legacy_bot: str, bot_ids: set[str]) -> str:
    if thread.startswith("subagent_"):
        return "a" + hashlib.sha256(thread.encode()).hexdigest()[:7]
    if (
        legacy_bot
        and thread.startswith("feishu-")
        and not any(thread.startswith(f"feishu-{bid}-") for bid in bot_ids)
    ):
        return f"feishu-{legacy_bot}-{thread[7:]}"[:63].rstrip("-")
    return thread


def rewrite_references(value, rename):
    """只处理结构化引用，不替换用户正文中的字符串。"""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = rename(key) if isinstance(key, str) else key
            if key in result:
                raise ValueError("会话引用迁移后出现重复键")
            result[key] = (
                rename(item)
                if key in _IDS and isinstance(item, str)
                else rewrite_references(item, rename)
            )
        return result
    if isinstance(value, list):
        return [rewrite_references(item, rename) for item in value]
    return value


def _merge_versions(source: dict, target: dict) -> None:
    """合并同一通道的版本，忽略已移除的 Todo。"""
    for channel, version in source.items():
        if channel != "todos":
            key = rename_channel(channel)
            target[key] = max(target.get(key, version), version)


class Transformer:
    def __init__(
        self, rename, links: dict, aliases: dict, model_profiles: dict | None = None
    ) -> None:
        self.rename = rename
        self.links = links
        self.aliases = aliases
        self.model_profiles = model_profiles or {}

    def identity(self, thread: str, identity: str) -> str:
        seen = set()
        while (thread, identity) in self.aliases:
            if identity in seen:
                raise ValueError("旧消息身份映射存在循环")
            seen.add(identity)
            identity = self.aliases[thread, identity]
        return identity

    def value(self, value, thread: str):
        if isinstance(value, _DeltaSnapshot):
            return _DeltaSnapshot(self.value(value.value, thread))
        if isinstance(value, Send):
            arg = self.value(value.arg, thread)
            if isinstance(arg, dict) and "messages" in arg:
                arg.pop("todos", None)
            return Send(_NODES.get(value.node, value.node), arg)
        if isinstance(value, BaseMessage):
            return self.message(value, thread, history_message_ids([value])[0])
        if isinstance(value, list):
            if value and all(isinstance(item, BaseMessage) for item in value):
                return [
                    self.message(item, thread, identity)
                    for item, identity in zip(value, history_message_ids(value))
                ]
            return [self.value(item, thread) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item, thread) for item in value)
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                renamed = rename_channel(key) if isinstance(key, str) else key
                item = self.value(item, thread)
                if renamed in _IDS and isinstance(item, str):
                    item = self.rename(item)
                if (
                    renamed in result
                    and isinstance(item, dict)
                    and isinstance(result[renamed], dict)
                ):
                    result[renamed].update(item)
                else:
                    result[renamed] = item
            if "v" in value and "channel_versions" in value:
                result.get("channel_values", {}).pop("todos", None)
                # 两个旧审批节点合并为同一个执行器，版本游标取已见的最大值。
                versions = {}
                _merge_versions(value["channel_versions"], versions)
                result["channel_versions"] = versions
                seen = {}
                for node, channels in value.get("versions_seen", {}).items():
                    _merge_versions(channels, seen.setdefault(rename_channel(node), {}))
                result["versions_seen"] = seen
            return result
        if dataclasses.is_dataclass(value):
            changes = {
                field.name: self.value(getattr(value, field.name), thread)
                for field in dataclasses.fields(value)
            }
            if "node" in changes:
                changes["node"] = _NODES.get(changes["node"], changes["node"])
            return dataclasses.replace(value, **changes)
        return value

    def message(self, message: BaseMessage, thread: str, identity: str) -> BaseMessage:
        kwargs = rewrite_references(message.additional_kwargs, self.rename)
        stable = self.identity(thread, identity)
        if stable != identity or HISTORY_ID_KEY in kwargs:
            kwargs[HISTORY_ID_KEY] = stable
        if (thread, identity) in self.links:
            checkpoint, exclude = self.links[thread, identity]
            kwargs[HISTORY_KEY] = {
                "checkpoint_id": checkpoint,
                "exclude": [self.identity(thread, i) for i in exclude],
            }
        elif HISTORY_KEY in kwargs:
            kwargs[HISTORY_KEY]["exclude"] = [
                self.identity(thread, i) for i in kwargs[HISTORY_KEY]["exclude"]
            ]
        changes = {"id": message.id or identity, "additional_kwargs": kwargs}
        if isinstance(message, AIMessage):
            origin = message.response_metadata.get(ORIGIN)
            if isinstance(origin, (tuple, list)) and len(origin) == 4:
                provider = self.model_profiles.get((origin[0], origin[3]), origin[0])
                if provider != origin[0]:
                    changes["response_metadata"] = {
                        **message.response_metadata,
                        ORIGIN: (provider, *origin[1:]),
                    }
            changes["tool_calls"] = [
                {
                    **call,
                    "name": "artifacts"
                    if call["name"] == "present_files"
                    else call["name"],
                    "args": rewrite_references(call["args"], self.rename),
                }
                for call in message.tool_calls
            ]
        if isinstance(message, ToolMessage):
            if message.name == "present_files":
                changes["name"] = "artifacts"
            if isinstance(message.content, str):
                try:
                    parsed = json.loads(message.content)
                except ValueError:
                    pass
                else:
                    rewritten = rewrite_references(parsed, self.rename)
                    if rewritten != parsed:
                        changes["content"] = json.dumps(rewritten, ensure_ascii=False)
        if message.type == "system" and str(message.content).startswith("<compact>"):
            kwargs.setdefault("lumi", {})["items"] = []
            return HumanMessage(content=message.content, **changes)
        return message.model_copy(update=changes)


def _messages(value):
    if isinstance(value, BaseMessage):
        yield value
    elif isinstance(value, _DeltaSnapshot):
        yield from _messages(value.value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _messages(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _messages(item)
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            yield from _messages(getattr(value, field.name))


async def _history_links(saver):
    links, aliases = {}, {}
    candidates = []
    # alist 在 yield 期间持锁，先收集配置再通过 graph 读取，避免重入同一把锁。
    async for saved in saver.alist(None):
        raw = [saved.checkpoint.get("channel_values", {}), saved.pending_writes]
        if not any(
            m.type in ("human", "system")
            and str(m.content).startswith("<compact>")
            and HISTORY_KEY not in m.additional_kwargs
            for m in _messages(raw)
        ):
            continue
        candidates.append(saved.config)
    if not candidates:
        return links, aliases
    from lumi.agents.core.graph import LumiAgent

    graph = LumiAgent(checkpointer=saver).graph
    for config in candidates:
        state = await graph.aget_state(config)
        compact = compact_message(state.values.get("messages", []))
        if compact is None or HISTORY_KEY in compact[0].additional_kwargs:
            continue
        thread = config["configurable"]["thread_id"]
        key = thread, compact[1]
        if key in links:
            continue
        previous = await find_history_link(
            graph, thread, config["configurable"]["checkpoint_id"], compact[1]
        )
        if previous is not None:
            links[key] = previous.checkpoint_id, previous.exclude
            aliases.update(
                {(thread, after): before for before, after in previous.aliases}
            )
    return links, aliases


@asynccontextmanager
async def _checkpoint_store(
    path: Path, backend: AgentsConfig, *, apply: bool
) -> AsyncIterator[tuple[BaseCheckpointSaver, Query, bool]]:
    """管理数据库连接与事务；检查模式只读，异常时回滚。"""
    postgres = backend.checkpoint == "postgres"
    with migration_serde() as serde:
        if postgres:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            conn = await AsyncConnection.connect(
                backend.postgres_uri,
                autocommit=False,
                prepare_threshold=0,
                row_factory=dict_row,
            )
        else:
            conn = await aiosqlite.connect(
                str(path) if apply else f"file:{path}?mode=ro", uri=not apply
            )
            conn.row_factory = aiosqlite.Row
        try:
            if postgres:
                saver = AsyncPostgresSaver(conn, serde=serde)
            else:
                saver = AsyncSqliteSaver(conn, serde=serde)
                saver.is_setup = True  # 检查旧数据时不执行 SDK 的建表/升级逻辑。
                columns = await (
                    await conn.execute("PRAGMA table_info(writes)")
                ).fetchall()
                saver._has_task_path = any(
                    row["name"] == "task_path" for row in columns
                )

            async def query(sql: str, params: tuple = ()):
                return await conn.execute(
                    sql if postgres else sql.replace("%s", "?"), params
                )

            await query("BEGIN IMMEDIATE" if apply and not postgres else "BEGIN")
            if postgres and not apply:
                await query("SET TRANSACTION READ ONLY")
            yield saver, query, postgres
            await (conn.commit() if apply else conn.rollback())
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()


def _row_changes(
    row: dict,
    column: str,
    transform: Transformer,
    serde: SerializerProtocol,
    *,
    postgres: bool,
) -> dict:
    """计算一条记录的变化，不执行 SQL，也不重新编码未变化的内容。"""
    raw = row[column]
    if raw is None or row["type"] == "empty":
        return {}
    value = (
        raw
        if postgres and column == "checkpoint"
        else serde.loads_typed((row["type"], bytes(raw)))
    )
    updated = transform.value(value, row["thread_id"])
    changes = {column: updated} if updated != value else {}
    if column == "checkpoint":
        metadata = row["metadata"] if postgres else json.loads(row["metadata"])
        updated = transform.value(metadata, row["thread_id"])
        if updated != metadata:
            changes["metadata"] = updated
    channel = row.get("channel")
    if channel is not None and rename_channel(channel) != channel:
        changes["channel"] = rename_channel(channel)
    return changes


def _encode_changes(
    changes: dict, column: str, serde: SerializerProtocol, *, postgres: bool
) -> dict:
    """只在实际写入时将变化编码为数据库字段。"""
    encoded = changes.copy()
    json_columns = {"metadata"}
    if postgres:
        from psycopg.types.json import Jsonb

        json_columns.add("checkpoint")
    for name in json_columns & encoded.keys():
        encoded[name] = (
            Jsonb(encoded[name]) if postgres else json.dumps(encoded[name]).encode()
        )
    if column in encoded and column not in json_columns:
        encoded["type"], encoded[column] = serde.dumps_typed(encoded[column])
    return encoded


async def _migrate_tables(
    saver: BaseCheckpointSaver,
    query: Query,
    *,
    postgres: bool,
    rename: Callable[[str], str],
    model_profiles: dict | None,
    apply: bool,
) -> int:
    links, aliases = await _history_links(saver)
    transform = Transformer(rename, links, aliases, model_profiles)
    tables = [("checkpoints", "thread_id checkpoint_ns checkpoint_id", "checkpoint")]
    if postgres:
        tables += [
            ("checkpoint_blobs", "thread_id checkpoint_ns channel version", "blob"),
            (
                "checkpoint_writes",
                "thread_id checkpoint_ns checkpoint_id task_id idx",
                "blob",
            ),
        ]
    else:
        tables += [
            ("writes", "thread_id checkpoint_ns checkpoint_id task_id idx", "value")
        ]
    count = 0
    for table, key_names, column in tables:
        keys = key_names.split()
        rows = await (await query(f"SELECT * FROM {table}")).fetchall()
        threads = {row["thread_id"] for row in rows}
        names = {thread: rename(thread) for thread in threads}
        if len(set(names.values())) != len(threads):
            raise ValueError("旧会话 ID 迁移后冲突")
        where = " AND ".join(f"{key}=%s" for key in keys)
        for stored in rows:
            row = dict(stored)
            args = tuple(row[key] for key in keys)
            if row.get("channel") == "todos":
                count += 1
                if apply:
                    await query(f"DELETE FROM {table} WHERE {where}", args)
                continue
            changes = _row_changes(
                row, column, transform, saver.serde, postgres=postgres
            )
            count += len(changes)
            if changes and apply:
                encoded = _encode_changes(
                    changes, column, saver.serde, postgres=postgres
                )
                fields = ", ".join(f"{key}=%s" for key in encoded)
                await query(
                    f"UPDATE {table} SET {fields} WHERE {where}",
                    (*encoded.values(), *args),
                )
        for old, new in names.items():
            if new != old:
                count += 1
                if apply:
                    await query(
                        f"UPDATE {table} SET thread_id=%s WHERE thread_id=%s",
                        (new, old),
                    )
    return count


async def migrate_checkpoints(
    directory: Path,
    *,
    rename: Callable[[str], str],
    apply: bool,
    model_profiles: dict | None = None,
) -> int:
    """迁移 checkpoint 和关联数据库，只返回变更数量。"""
    from lumi.utils.config import get_config

    backend = get_config().config.agents
    path = directory / "checkpoints.db"
    count = 0
    if path.exists() or backend.checkpoint == "postgres":
        async with _checkpoint_store(path, backend, apply=apply) as (
            saver,
            query,
            postgres,
        ):
            count = await _migrate_tables(
                saver,
                query,
                postgres=postgres,
                rename=rename,
                model_profiles=model_profiles,
                apply=apply,
            )
    return count + _local_databases(directory, rename, apply=apply)


def _local_databases(directory: Path, rename, *, apply: bool) -> int:
    """旧子代理表结构和本地结构化会话引用。"""
    count = 0
    for path in directory.glob("*.db"):
        if path.name == "checkpoints.db":
            continue
        conn = sqlite3.connect(
            str(path) if apply else f"file:{path}?mode=ro", uri=not apply
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            if "dream_cursor" in tables:
                count += 1
                if apply:
                    conn.execute("DROP TABLE dream_cursor")
            for table in tables:
                if table == "dream_cursor":
                    continue
                columns = {
                    row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
                }
                if table == "subagents" and "title" not in columns:
                    count += 1
                    if apply:
                        conn.execute(
                            "ALTER TABLE subagents ADD COLUMN title TEXT NOT NULL DEFAULT ''"
                        )
                id_columns = columns & {"thread_id", "agent_id", "parent_id"}
                for column in id_columns:
                    for row in conn.execute(
                        f'SELECT DISTINCT "{column}" AS identity FROM "{table}"'
                    ).fetchall():
                        old = row["identity"]
                        if isinstance(old, str) and rename(old) != old:
                            count += 1
                            if apply:
                                conn.execute(
                                    f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=?',
                                    (rename(old), old),
                                )
                if table in ("subagent_events", "subagents"):
                    events = table == "subagent_events"
                    key, column = (
                        ("seq", "payload") if events else ("agent_id", "handle")
                    )
                    fields = f"{key},type,{column}" if events else f"{key},{column}"
                    for row in conn.execute(f"SELECT {fields} FROM {table}").fetchall():
                        original = json.loads(row[column])
                        updated = rewrite_references(original, rename)
                        if (
                            events
                            and row["type"] in ("tool.start", "tool.complete")
                            and updated.get("name") == "present_files"
                        ):
                            updated["name"] = "artifacts"
                        if updated != original:
                            count += 1
                            if apply:
                                conn.execute(
                                    f"UPDATE {table} SET {column}=? WHERE {key}=?",
                                    (json.dumps(updated, ensure_ascii=False), row[key]),
                                )
            if apply:
                conn.commit()
            else:
                conn.rollback()
        finally:
            conn.close()
    return count

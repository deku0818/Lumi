"""Host-local subagent sessions, FIFO submissions, display events and durable returns.

The SQLite store owns task truth; in-memory tasks and background-task cards are projections.
Opening the store recovers records, never restarts work.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from secrets import choice
from string import ascii_lowercase, digits
from typing import Any

from lumi.agents.runtime.subagents.types import (
    TERMINAL_STATUSES,
    Subagent,
    SubagentTurn,
    TurnStatus,
)

_ID_CHARACTERS = ascii_lowercase + digits


class SubagentStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS subagents (
                agent_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL,
                workspace TEXT NOT NULL, name TEXT NOT NULL, executor TEXT NOT NULL,
                definition TEXT NOT NULL, depth INTEGER NOT NULL,
                env_extra TEXT NOT NULL, tool_mode TEXT NOT NULL,
                handle TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS subagents_parent ON subagents(parent_id);
            CREATE TABLE IF NOT EXISTS subagent_turns (
                turn_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL,
                notify INTEGER NOT NULL, request_key TEXT UNIQUE,
                output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, finished_at REAL,
                UNIQUE(agent_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS subagent_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
                turn_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS subagent_events_agent ON subagent_events(agent_id, seq);
            CREATE TABLE IF NOT EXISTS subagent_deliveries (
                delivery_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL,
                turn_id TEXT UNIQUE NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS subagent_deliveries_parent
                ON subagent_deliveries(parent_id, delivered);
        """)

    def close(self) -> None:
        self.conn.close()

    def _new_id(self, table: str, column: str, prefix: str) -> str:
        while True:
            value = prefix + "".join(choice(_ID_CHARACTERS) for _ in range(7))
            existing = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE {column}=?", (value,)
            ).fetchone()
            if existing is None:
                return value

    def agent(self, agent_id: str) -> Subagent | None:
        row = self.conn.execute(
            "SELECT * FROM subagents WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["definition"] = json.loads(data["definition"])
        data["handle"] = json.loads(data["handle"])
        return Subagent(**data)

    def owned_agent(self, parent_id: str, agent_id: str) -> Subagent:
        agent = self.agent(agent_id)
        if agent is None or agent.parent_id != parent_id:
            raise ValueError("子代理不存在或不属于当前会话")
        return agent

    def create(
        self,
        parent_id: str,
        workspace: str,
        definition: dict,
        depth: int,
        env_extra: str,
        tool_mode: str,
        prompt: str,
        notify: bool,
        request_key: str | None = None,
    ) -> SubagentTurn:
        if previous := self.request(request_key):
            owner = self.agent(previous.agent_id)
            if (
                owner.parent_id != parent_id
                or owner.workspace != workspace
                or owner.definition != definition
                or previous.prompt != prompt
            ):
                raise ValueError("请求标识已用于另一条创建请求")
            return previous
        agent_id = self._new_id("subagents", "agent_id", "a")
        with self.conn:
            self.conn.execute(
                "INSERT INTO subagents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    agent_id,
                    parent_id,
                    workspace,
                    definition["name"],
                    definition.get("executor", "lumi"),
                    json.dumps(definition),
                    depth,
                    env_extra,
                    tool_mode,
                    "{}",
                    time.time(),
                ),
            )
            return self._submit(agent_id, prompt, notify, request_key)

    def submit(
        self,
        agent_id: str,
        prompt: str,
        notify: bool,
        request_key: str | None = None,
    ) -> SubagentTurn:
        if previous := self.request(request_key):
            if previous.agent_id != agent_id or previous.prompt != prompt:
                raise ValueError("请求标识已用于另一条消息")
            return previous
        with self.conn:
            return self._submit(agent_id, prompt, notify, request_key)

    def _submit(
        self,
        agent_id: str,
        prompt: str,
        notify: bool,
        request_key: str | None,
    ) -> SubagentTurn:
        ordinal = self.conn.execute(
            "SELECT COALESCE(MAX(ordinal),0)+1 FROM subagent_turns WHERE agent_id=?",
            (agent_id,),
        ).fetchone()[0]
        turn_id = self._new_id("subagent_turns", "turn_id", "t")
        self.conn.execute(
            "INSERT INTO subagent_turns (turn_id,agent_id,ordinal,prompt,status,notify,"
            "request_key,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                turn_id,
                agent_id,
                ordinal,
                prompt,
                "queued",
                int(notify),
                request_key,
                time.time(),
            ),
        )
        self._event(
            agent_id,
            turn_id,
            "message.submitted",
            {
                "message_id": turn_id + ":input",
                "source": "parent_agent",
                "text": prompt,
                "ordinal": ordinal,
            },
        )
        return self.turn(turn_id)

    def request(self, request_key: str | None) -> SubagentTurn | None:
        if not request_key:
            return None
        row = self.conn.execute(
            "SELECT * FROM subagent_turns WHERE request_key=?", (request_key,)
        ).fetchone()
        return self._turn(row) if row else None

    @staticmethod
    def _turn(row: sqlite3.Row) -> SubagentTurn:
        data = dict(row)
        data.pop("request_key")
        data["notify"] = bool(data["notify"])
        return SubagentTurn(**data)

    def turn(self, turn_id: str) -> SubagentTurn:
        row = self.conn.execute(
            "SELECT * FROM subagent_turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if row is None:
            raise ValueError("子代理轮次不存在")
        return self._turn(row)

    def start(self, turn_id: str) -> SubagentTurn:
        turn = self.turn(turn_id)
        with self.conn:
            self.conn.execute(
                "UPDATE subagent_turns SET status='running' WHERE turn_id=?", (turn_id,)
            )
            self._event(
                turn.agent_id,
                turn_id,
                "turn.start",
                {
                    "message_id": turn_id + ":input",
                    "ordinal": turn.ordinal,
                },
            )
        return self.turn(turn_id)

    def finish(
        self,
        turn_id: str,
        status: TurnStatus,
        output: str = "",
        error: str = "",
    ) -> None:
        turn = self.turn(turn_id)
        if turn.status in TERMINAL_STATUSES:
            return
        agent = self.agent(turn.agent_id)
        with self.conn:
            self.conn.execute(
                "UPDATE subagent_turns SET status=?,output=?,error=?,finished_at=? "
                "WHERE turn_id=?",
                (status, output, error, time.time(), turn_id),
            )
            self._event(
                turn.agent_id,
                turn_id,
                "turn.complete",
                {
                    "status": status,
                    "output": output,
                    "error": error,
                    "ordinal": turn.ordinal,
                },
            )
            if turn.notify:
                self.conn.execute(
                    "INSERT OR IGNORE INTO subagent_deliveries VALUES (?,?,?,0)",
                    (turn_id, agent.parent_id, turn_id),
                )

    def recover(self) -> None:
        """A previous runtime cannot prove any unfinished work; do not replay it."""
        rows = self.conn.execute(
            "SELECT turn_id,status FROM subagent_turns WHERE status IN ('queued','running')"
        ).fetchall()
        for row in rows:
            notification = self.is_notification(row["turn_id"])
            if row["status"] == "queued" and notification:
                continue
            status = "interrupted" if row["status"] == "running" else "cancelled"
            self.finish(
                row["turn_id"],
                status,
                output=self.turn(row["turn_id"]).prompt if notification else "",
                error="后端已重启，本轮未自动重做",
            )

    def is_notification(self, turn_id: str) -> bool:
        key = self.conn.execute(
            "SELECT request_key FROM subagent_turns WHERE turn_id=?",
            (turn_id,),
        ).fetchone()[0]
        return bool(key and key.startswith(("return:", "background:")))

    def queued_notifications(self) -> list[SubagentTurn]:
        rows = self.conn.execute(
            "SELECT * FROM subagent_turns WHERE status='queued' AND "
            "(request_key LIKE 'return:%' OR request_key LIKE 'background:%') ORDER BY created_at,ordinal"
        ).fetchall()
        return [self._turn(row) for row in rows]

    def agent_ids(self) -> list[str]:
        return [row[0] for row in self.conn.execute("SELECT agent_id FROM subagents")]

    def save_handle(self, agent_id: str, handle: dict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE subagents SET handle=? WHERE agent_id=?",
                (json.dumps(handle), agent_id),
            )

    def delete_agent(self, agent_id: str) -> list[str]:
        turns = [
            row[0]
            for row in self.conn.execute(
                "SELECT turn_id FROM subagent_turns WHERE agent_id=?",
                (agent_id,),
            )
        ]
        with self.conn:
            self.conn.execute(
                "DELETE FROM subagent_deliveries WHERE turn_id IN "
                "(SELECT turn_id FROM subagent_turns WHERE agent_id=?)",
                (agent_id,),
            )
            for table in ("subagent_events", "subagent_turns", "subagents"):
                self.conn.execute(f"DELETE FROM {table} WHERE agent_id=?", (agent_id,))
        return turns

    def _event(self, agent_id: str, turn_id: str, kind: str, payload: dict) -> dict:
        cursor = self.conn.execute(
            "INSERT INTO subagent_events (agent_id,turn_id,type,payload) VALUES (?,?,?,?)",
            (
                agent_id,
                turn_id,
                kind,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        return {
            "seq": cursor.lastrowid,
            "agent_id": agent_id,
            "turn_id": turn_id,
            "type": kind,
            "payload": payload,
        }

    def event(self, agent_id: str, turn_id: str, kind: str, payload: dict) -> dict:
        with self.conn:
            return self._event(agent_id, turn_id, kind, payload)

    def history(self, agent_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM subagent_events WHERE agent_id=? AND seq>? ORDER BY seq LIMIT ?",
            (agent_id, after, min(max(limit, 1), 500)),
        ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def list_agents(self, parent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT agent_id FROM subagents WHERE parent_id=? ORDER BY created_at",
            (parent_id,),
        ).fetchall()
        results = []
        for row in rows:
            agent = self.agent(row["agent_id"])
            latest = self._turn(
                self.conn.execute(
                    "SELECT * FROM subagent_turns WHERE agent_id=? ORDER BY ordinal DESC LIMIT 1",
                    (agent.agent_id,),
                ).fetchone()
            )
            active = self.conn.execute(
                "SELECT status FROM subagent_turns WHERE agent_id=? AND status IN ('queued','running') ORDER BY ordinal LIMIT 1",
                (agent.agent_id,),
            ).fetchone()
            results.append(
                {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "executor": agent.executor,
                    "cwd": agent.workspace,
                    "status": active["status"] if active else "idle",
                    "turn_count": latest.ordinal,
                    "last_turn_id": latest.turn_id,
                    "last_turn_status": latest.status,
                    "summary": (latest.output or latest.error)[:1000],
                }
            )
        return results

    def pending_deliveries(self, parent_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT d.delivery_id,d.turn_id,d.delivered AS delivery_state,a.name,a.executor FROM subagent_deliveries d "
            "JOIN subagent_turns t ON t.turn_id=d.turn_id "
            "JOIN subagents a ON a.agent_id=t.agent_id "
            "WHERE d.parent_id=? AND d.delivered!=1 ORDER BY t.created_at",
            (parent_id,),
        ).fetchall()
        return [{**dict(row), **asdict(self.turn(row["turn_id"]))} for row in rows]

    def has_deliveries(self, parent_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM subagent_deliveries WHERE parent_id=? AND delivered!=1 LIMIT 1",
                (parent_id,),
            ).fetchone()
            is not None
        )

    def presented(self, parent_id: str, delivery_ids: list[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE subagent_deliveries SET delivered=2 WHERE parent_id=? "
                "AND delivery_id=? AND delivered=0",
                [(parent_id, key) for key in delivery_ids],
            )

    def acknowledge(self, parent_id: str, delivery_ids: list[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE subagent_deliveries SET delivered=1 WHERE parent_id=? AND delivery_id=?",
                [(parent_id, key) for key in delivery_ids],
            )

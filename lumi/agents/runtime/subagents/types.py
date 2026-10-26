"""Persistent subagent identities and the small executor contract (no Gateway dependency)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ExecutorKind = Literal["lumi", "codex", "claude_code"]
TurnStatus = Literal[
    "queued", "running", "completed", "failed", "cancelled", "interrupted", "blocked"
]
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "blocked"}
)


@dataclass(frozen=True)
class Subagent:
    agent_id: str
    parent_id: str
    workspace: str  # Instance cwd; ownership is determined solely by parent_id.
    name: str
    executor: ExecutorKind
    definition: dict[str, Any]
    depth: int
    env_extra: str
    tool_mode: str
    handle: dict[str, Any]
    created_at: float
    title: str = ""


@dataclass(frozen=True)
class SubagentTurn:
    turn_id: str
    agent_id: str
    ordinal: int
    prompt: str
    status: TurnStatus
    notify: bool
    output: str
    error: str
    created_at: float
    finished_at: float | None


type Emit = Callable[[str, dict[str, Any]], None]
type SaveHandle = Callable[[dict[str, Any]], None]


class SubagentExecutor(Protocol):
    async def run(
        self, agent: Subagent, turn: SubagentTurn, emit: Emit, save_handle: SaveHandle
    ) -> str: ...

    async def close(self) -> None: ...


class SubagentBlockedError(RuntimeError):
    """The native agent needs an interaction this read-only entry cannot answer."""


class SubagentQuestionError(SubagentBlockedError):
    """End this turn with questions; the parent replies through agent_message."""

    def __init__(self, questions: list[dict[str, Any]]) -> None:
        self.questions = questions
        super().__init__("子代理需要补充信息，问题已交回主 Agent")


class SubagentDeliveryUnknownError(RuntimeError):
    """Transport failed after submission; never automatically send the prompt again."""

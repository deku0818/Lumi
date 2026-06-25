"""ACP client —— 把外部编程 agent（Claude Code、Codex、Gemini CLI…）当子进程拉起来派活。

与 MCP client 对称：**MCP 让 Lumi 用外部工具，ACP 让 Lumi 用外部 agent**。
纯传输层，构建在官方 `acp` SDK 之上，不依赖 LangGraph，可独立单测（起子进程跑通握手）。

生命周期（MVP）：每次委派 spawn 一个子进程、用完即关——与无 checkpointer 的子 agent 一致。
"""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from acp import (
    PROTOCOL_VERSION,
    Client,
    RequestPermissionResponse,
    default_environment,
    spawn_agent_process,
    text_block,
)
from acp.schema import DeniedOutcome

# (session_id, update) —— 每条 ACP session/update 的流式回调。
# PR2 把它接到 `adispatch_custom_event`，使外部 agent 事件回流进前端子卡片。
UpdateHandler = Callable[[str, object], Awaitable[None]]


@dataclass(frozen=True)
class AcpResult:
    """一次委派的收尾：stop_reason + agent 累积的回复文本。"""

    stop_reason: str
    text: str


def _agent_message_text(update: object) -> str:
    """从 session/update 里抽 agent 回复正文（只取 agent_message_chunk 的文本块）。"""
    if getattr(update, "session_update", None) != "agent_message_chunk":
        return ""
    content = getattr(update, "content", None)
    if getattr(content, "type", None) == "text":
        return content.text
    return ""


class _BridgeClient(Client):
    """把 ACP agent 的回调接到 Lumi。PR1 只接 session_update（事件流）；
    权限（PR3）与 fs（PR4）尚未接入，此处给 fail-safe 默认：一律拒绝。"""

    def __init__(self, on_update: UpdateHandler) -> None:
        self._on_update = on_update

    async def session_update(self, session_id, update, **kwargs):
        await self._on_update(session_id, update)

    async def request_permission(self, options, session_id, tool_call, **kwargs):
        # PR3 接 PermissionEngine + ApprovalBroker；在此之前外部 agent 的工具一律拒绝。
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


class AcpClient:
    """驱动一个外部 ACP agent 子进程。

    `command/args/env` 沿用 `acp_agents.json`（与 `mcp_server.json` 对称）的形状；
    认证由 adapter 自己 owns（env 透传），Lumi 不管外部 agent 的 auth。
    """

    def __init__(
        self,
        command: str,
        *args: str,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = args
        # 给了 env 就并入当前环境（否则子进程丢 PATH，npx 起不来）。
        self._env = {**default_environment(), **env} if env else None

    @asynccontextmanager
    async def _connect(self, on_update: UpdateHandler):
        async with spawn_agent_process(
            _BridgeClient(on_update), self._command, *self._args, env=self._env
        ) as (conn, _proc):
            await conn.initialize(protocol_version=PROTOCOL_VERSION)
            yield conn

    async def run(
        self,
        task: str,
        cwd: str,
        on_update: UpdateHandler | None = None,
    ) -> AcpResult:
        """spawn → initialize → session/new(cwd) → session/prompt(task) → 收 stop_reason。

        `cwd` 每次由调用方（LumiAgent）显式指定。`on_update` 为可选的流式旁路
        （PR2 接事件回流）；无论是否提供，agent 正文都会被累积进返回的 `AcpResult.text`。
        """
        chunks: list[str] = []

        async def collect(session_id: str, update: object) -> None:
            chunks.append(_agent_message_text(update))
            if on_update is not None:
                await on_update(session_id, update)

        async with self._connect(collect) as conn:
            session = await conn.new_session(cwd=cwd)
            resp = await conn.prompt(
                prompt=[text_block(task)], session_id=session.session_id
            )
            return AcpResult(stop_reason=resp.stop_reason, text="".join(chunks))

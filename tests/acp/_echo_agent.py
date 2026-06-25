"""最小 ACP agent 子进程，仅供 AcpClient 单测起握手用。

实现 initialize / new_session / prompt：prompt 时回发一条 agent_message_chunk
（把任务文本回显），再以 end_turn 收尾。脱离 Lumi、无网络、无危险副作用。
"""

import asyncio

from acp import (
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    run_agent,
    update_agent_message_text,
)
from acp.interfaces import Agent
from acp.schema import AgentCapabilities


class EchoAgent(Agent):
    def __init__(self) -> None:
        self._conn = None

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def initialize(self, protocol_version, **kwargs) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd, **kwargs) -> NewSessionResponse:
        return NewSessionResponse(session_id="echo-session")

    async def prompt(self, prompt, session_id, **kwargs) -> PromptResponse:
        task = next((b.text for b in prompt if getattr(b, "type", None) == "text"), "")
        await self._conn.session_update(
            session_id, update_agent_message_text(f"echo: {task}")
        )
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(EchoAgent()))

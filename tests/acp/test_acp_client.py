"""AcpClient 传输层单测：起一个真子进程（_echo_agent.py）跑通 ACP 握手 + prompt。

脱离 Lumi 运行时——只验 `lumi/acp/` 这层能 spawn → initialize → session/new →
session/prompt → 解析 session/update → 收 stop_reason。
"""

import sys
from pathlib import Path

from lumi.acp import AcpClient, AcpResult

_ECHO_AGENT = str(Path(__file__).parent / "_echo_agent.py")


def _echo_client() -> AcpClient:
    return AcpClient(sys.executable, _ECHO_AGENT)


async def test_run_completes_handshake_and_collects_text(tmp_path):
    result = await _echo_client().run("hello world", cwd=str(tmp_path))

    assert isinstance(result, AcpResult)
    assert result.stop_reason == "end_turn"
    assert result.text == "echo: hello world"


async def test_on_update_streams_session_updates(tmp_path):
    seen: list[object] = []

    async def on_update(session_id, update):
        seen.append(update)

    result = await _echo_client().run("ping", cwd=str(tmp_path), on_update=on_update)

    assert result.text == "echo: ping"
    # echo agent 回发一条 agent_message_chunk
    assert any(
        getattr(u, "session_update", None) == "agent_message_chunk" for u in seen
    )


async def test_cwd_is_passed_per_run(tmp_path):
    # 两次委派各自指定 cwd，互不影响（生命周期：每次 spawn 一个子进程用完即关）。
    a = await _echo_client().run("a", cwd=str(tmp_path))
    b = await _echo_client().run("b", cwd=str(tmp_path))
    assert (a.text, b.text) == ("echo: a", "echo: b")

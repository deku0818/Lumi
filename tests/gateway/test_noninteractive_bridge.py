"""无人应答的 bridge（cron / lumi -p）：需人工审批的调用自动拒绝、本轮正常结束。

真实 LumiAgent 图 + 真实 AgentBridge，只假 create_llm。此前 bridge 恒接审批通道，
无人应答的入口遇受保护写入 / ask 规则即挂起——cron 挂满 100 分钟超时，lumi -p 永久无输出。
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from lumi.gateway.bridge import AgentBridge
from tests.gateway.test_message_retry_e2e import ScriptedLLM, _no_tools


@pytest.fixture(autouse=True)
def _isolated(isolated_config):
    """真 initialize 走 sqlite checkpointer：隔离 ~/.lumi。"""


@pytest.mark.asyncio
async def test_protected_write_is_auto_rejected_without_approval_channel(tmp_path):
    write_hooks = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "write",
                "args": '{"file_path": ".lumi/hooks.json", "content": "{}"}',
                "id": "c1",
                "index": 0,
            }
        ],
    )
    script = [[write_hooks], [AIMessageChunk(content="好的，不改了")]]
    bridge = AgentBridge()
    with (
        patch("lumi.models.chain.create_llm", return_value=ScriptedLLM(script=script)),
        patch.object(AgentBridge, "_build_tools", new=_no_tools),
    ):
        await bridge.initialize(project_dir=str(tmp_path), interactive=False)

        async def collect():
            return [
                e async for e in bridge.stream_response("改下配置", tool_mode="auto")
            ]

        events = await asyncio.wait_for(collect(), timeout=30)
    kinds = [e.kind.value for e in events]
    assert "approval.request" not in kinds
    assert kinds[-1] == "turn.complete"
    state = await bridge._agent.graph.aget_state(bridge._config)
    [rejected] = [m for m in state.values["messages"] if isinstance(m, ToolMessage)]
    assert "无交互式审批通道" in rejected.content
    assert not (tmp_path / ".lumi" / "hooks.json").exists()

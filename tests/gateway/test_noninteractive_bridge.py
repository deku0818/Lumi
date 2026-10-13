"""真实 bridge：配置写入和普通文件一样按规则审批，无人工通道也不挂起。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.tools import tool

from lumi.gateway.bridge import AgentBridge
from tests.gateway.test_message_retry_e2e import ScriptedLLM


@pytest.fixture(autouse=True)
def _isolated(isolated_config):
    """真 initialize 走 sqlite checkpointer：隔离 ~/.lumi。"""


@pytest.mark.parametrize(
    "mode,rule,ai_decision,allowed,classify",
    [
        ("auto", "ask", "approve", True, True),
        ("auto", "ask", "reject", False, True),
        ("auto", "allow", "reject", True, False),
        ("privileged", "ask", "reject", True, False),
        ("auto", "deny", "approve", False, False),
        ("privileged", "deny", "approve", False, False),
    ],
)
async def test_config_write_obeys_same_rules_without_approval_channel(
    tmp_path, mode, rule, ai_decision, allowed, classify
):
    config_dir = tmp_path / ".lumi"
    config_dir.mkdir()
    (config_dir / "permissions.json").write_text(
        json.dumps({"permissions": {rule: ["write"]}})
    )

    @tool
    async def write(file_path: str, content: str):
        """写入临时项目中的文件。"""
        (tmp_path / file_path).write_text(content)
        return "written"

    async def build_tools(self, wait_mcp=False):
        return [write]

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
    script = [[write_hooks], [AIMessageChunk(content="完成")]]
    classifier = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(decision=ai_decision, reason="测试裁决")
        )
    )
    bridge = AgentBridge()
    try:
        with (
            patch(
                "lumi.models.chain.create_llm", return_value=ScriptedLLM(script=script)
            ),
            patch.object(AgentBridge, "_build_tools", new=build_tools),
            patch("lumi.agents.core.nodes.structured_output", return_value=classifier),
        ):
            await bridge.initialize(project_dir=str(tmp_path), interactive=False)

            async def collect():
                return [
                    e async for e in bridge.stream_response("改下配置", tool_mode=mode)
                ]

            events = await asyncio.wait_for(collect(), timeout=5)
        kinds = [e.kind.value for e in events]
        assert "approval.request" not in kinds
        assert kinds[-1] == "turn.complete"
        state = await bridge.graph.aget_state(bridge._config)
        [result] = [m for m in state.values["messages"] if isinstance(m, ToolMessage)]
        assert (result.content == "written") == allowed
        assert (config_dir / "hooks.json").exists() == allowed
        assert classifier.ainvoke.await_count == int(classify)
    finally:
        await bridge.close()

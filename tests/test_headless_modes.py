"""无人应答的执行入口（lumi -p / dream）：auto 模式、不接审批通道（回归）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage


def _headless_bridge(captured: dict):
    class _Bridge:
        async def initialize(self, **kw) -> None:
            captured["init"] = kw

        async def stream_response(self, prompt, **kw):
            captured["stream"] = kw
            return
            yield

        async def close(self) -> None: ...

    return _Bridge


def test_cli_prompt_defaults_to_auto_without_approval_channel() -> None:
    # 此前默认 default 模式且接着审批通道：遇写操作即无输出永久挂起
    from lumi.cli import _run_headless

    captured: dict = {}
    with (
        patch("lumi.gateway.bridge.AgentBridge", _headless_bridge(captured)),
        patch("lumi.gateway.toolbox.inject_path"),
        patch("lumi.cli._export_lumi_bin"),
    ):
        _run_headless("hi")
    assert captured["stream"]["tool_mode"] == "auto"
    assert captured["init"]["interactive"] is False


def test_cli_privileged_flag_still_wins() -> None:
    from lumi.cli import _run_headless

    captured: dict = {}
    with (
        patch("lumi.gateway.bridge.AgentBridge", _headless_bridge(captured)),
        patch("lumi.gateway.toolbox.inject_path"),
        patch("lumi.cli._export_lumi_bin"),
    ):
        _run_headless("hi", privileged=True)
    assert captured["stream"]["tool_mode"] == "privileged"


async def test_dream_agent_runs_in_auto_mode(tmp_path: Path) -> None:
    # dream 的输入含群消息等不受信内容，不再以 privileged 运行；写记忆目录仍免审批
    from lumi.agents.memory.dream import _run_dream_fork

    seen: dict = {}

    async def fake_ainvoke(inputs, context=None):
        seen["mode"] = context.tool_mode
        return {"messages": [AIMessage(content="ok")]}

    ctx = SimpleNamespace(permission_engine=None, tool_mode="default")
    lumi_agent = SimpleNamespace(graph=SimpleNamespace(ainvoke=fake_ainvoke))
    with (
        patch(
            "lumi.agents.core.graph.create_agent",
            AsyncMock(return_value=(lumi_agent, ctx)),
        ),
        patch("lumi.agents.tools.get_tools", AsyncMock(return_value=[])),
    ):
        await _run_dream_fork(
            tmp_path, [], "p", label="dream", notify=False, record=lambda: None
        )
    assert seen["mode"] == "auto"


def test_cli_prints_only_main_agent_text(capsys) -> None:
    # 回归：lumi -p 不过滤子代理事件，子代理的流式正文混进 stdout
    from lumi.cli import _run_headless
    from lumi.gateway.bridge import BridgeEvent, EventKind

    class _Bridge:
        async def initialize(self, **kw) -> None: ...

        async def stream_response(self, prompt, **kw):
            yield BridgeEvent(
                kind=EventKind.MESSAGE_DELTA, text="子代理正文", parent_run_id="r"
            )
            yield BridgeEvent(kind=EventKind.MESSAGE_DELTA, text="主回答")

        async def close(self) -> None: ...

    with (
        patch("lumi.gateway.bridge.AgentBridge", _Bridge),
        patch("lumi.gateway.toolbox.inject_path"),
        patch("lumi.cli._export_lumi_bin"),
    ):
        _run_headless("hi")
    assert capsys.readouterr().out == "主回答\n"

"""bridge 跨轮状态不串（回归）：真实 LumiAgent 图 + 真实 AgentBridge，只假 create_llm。"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from lumi.gateway.bridge import AgentBridge
from tests.gateway.test_message_retry_e2e import ScriptedLLM, _no_tools


@pytest.fixture(autouse=True)
def _isolated(isolated_config):
    """真 initialize 走 sqlite checkpointer：隔离 ~/.lumi。"""


@pytest.fixture
async def bridge():
    b = AgentBridge()
    yield b
    await b.close()  # 不关的话 aiosqlite 的非守护线程让进程退不出


class _BreaksMidStream(ScriptedLLM):
    async def _astream(self, messages, stop=None, run_manager: Any = None, **kw):
        yield ChatGenerationChunk(message=AIMessageChunk(content="Hello wor"))
        raise ValueError("provider exploded")


async def test_error_turn_clears_partial_reply_buffer(tmp_path, bridge):
    # 回归：模型流到一半报错，半截正文留在 buffer 里；下一轮在首个 CallModel 之前按停，
    # 这段上一轮的残片会被当成本轮的中断回复写进 checkpoint
    with (
        patch("lumi.models.chain.create_llm", return_value=_BreaksMidStream()),
        patch.object(AgentBridge, "_build_tools", new=_no_tools),
    ):
        await bridge.initialize(project_dir=str(tmp_path))
        kinds = [e.kind.value async for e in bridge.stream_response("hi")]
    assert kinds[-1] == "error"
    assert bridge._partial_chunks == []


async def test_offline_flush_clears_ptl_retry(tmp_path, bridge):
    # 回归：PTL 重试中途被 stop / rewind 写回后 ptl_retry 留在 state 里，下一轮被迫
    # 无视阈值做一次有损压缩
    with patch.object(AgentBridge, "_build_tools", new=_no_tools):
        await bridge.initialize(project_dir=str(tmp_path))
    await bridge.flush_offline({"ptl_retry": True})
    await bridge.flush_offline({"messages": [AIMessage(content="半截")]})
    state = await bridge.graph.aget_state(bridge._config)
    assert state.values.get("ptl_retry") is False


async def test_set_workspace_rebuilds_project_prompt_and_memory(tmp_path, bridge):
    # 回归：切项目只 rebase 了权限引擎，系统提示词（项目层 SOUL）与记忆目录仍是旧项目的
    from lumi.agents.memory import memory_dir

    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        (d / ".lumi" / "prompts").mkdir(parents=True)
        (d / ".lumi" / "prompts" / "SOUL.md").write_text(f"soul-{d.name}")
    with patch.object(AgentBridge, "_build_tools", new=_no_tools):
        await bridge.initialize(project_dir=str(a))
        await bridge.folders.set_workspace(str(b))
    prompt = bridge._context.system_prompt
    assert "soul-b" in prompt and "soul-a" not in prompt
    assert str(memory_dir(b)) in prompt


async def _send(bridge: AgentBridge, gen) -> str:
    """跑完一轮，返回本轮用户消息落进 checkpoint 的正文。"""
    message_id = ""
    async for e in gen:
        message_id = message_id or e.message_id
    messages = await bridge.snapshot_messages()
    return str(next(m for m in messages if m.id == message_id).content)


async def test_folder_reminder_follows_history_not_bridge_memory(tmp_path, bridge):
    # 回归：「已通知」存在 bridge 内存里——重新生成删掉携带提醒的消息后不再重发，
    # 同 thread 换一个 bridge（重连）也不知道模型见过什么
    extra = tmp_path / "extra"
    extra.mkdir()
    reply = [[AIMessageChunk(content="ok")]]
    with (
        patch("lumi.models.chain.create_llm", return_value=ScriptedLLM(script=reply)),
        patch.object(AgentBridge, "_build_tools", new=_no_tools),
    ):
        await bridge.initialize(project_dir=str(tmp_path))
        bridge.folders.add_folder(str(extra))
        first_id = ""
        async for e in bridge.stream_response("hi"):
            first_id = first_id or e.message_id
        regen = await _send(bridge, bridge.stream_regenerate(first_id))
        assert str(extra) in regen  # 重答轮重新告知
        again = await _send(bridge, bridge.stream_response("again"))
        assert str(extra) not in again  # 历史里已有，不重复

        other = AgentBridge()
        try:
            await other.initialize(project_dir=str(tmp_path))
            other.switch_thread(bridge.current_thread_id)
            text = await _send(other, other.stream_response("x"))
        finally:
            await other.close()
    assert "移除" in text and str(extra) in text


async def test_user_turns_share_one_start_snapshot(tmp_path, bridge):
    from unittest.mock import AsyncMock

    with (
        patch(
            "lumi.models.chain.create_llm",
            return_value=ScriptedLLM(script=[[AIMessageChunk(content="ok")]]),
        ),
        patch.object(AgentBridge, "_build_tools", new=_no_tools),
    ):
        await bridge.initialize(project_dir=str(tmp_path))
        graph = bridge.graph
        reads = AsyncMock(wraps=graph.aget_state)
        with patch.object(graph, "aget_state", reads):
            for make_turn in (
                lambda: bridge.stream_response("hi"),
                lambda: bridge.stream_regenerate(message_id),
                lambda: bridge.stream_edit_resend(message_id, "edited"),
            ):
                reads.reset_mock()
                events = [e async for e in make_turn()]
                message_id = next(e.message_id for e in events if e.message_id)
                # 一次开轮快照（提醒 + 残留修复/截断共用），一次收尾 usage 快照。
                assert reads.await_count == 2

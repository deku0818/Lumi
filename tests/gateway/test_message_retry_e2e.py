"""畸形 tool_call 丢弃重试的端到端契约：真实 LumiAgent 图 + 真实 AgentBridge。

假的只有 create_llm 一处，prompt / bind_tools / _with_retry / astream_events / 图拓扑 /
bridge 事件翻译全是真的。空壳形状不手搓——把 ``name=None, id=None`` 的 tool_call_chunk
交给 LangChain 自己聚合，得到的正是客户现场 qwen 的 ``{'name': '', 'id': None}``。

锁两件单测锁不住的事：
1. 畸形响应不进 checkpoint（state 断言，不是 mock 的返回值）；
2. **事件次序** ``message.complete`` → ``message.retry`` → ``message.start``。
   畸形那次调用的 complete 先于 retry 到达，前端此时已把 streaming 清成 false，故
   两端回滚都必须靠 message.start 记边界，不能靠「streaming 中」反推——这条次序
   曾让 desktop 的 retry 分支一个都删不掉（乱码留在屏幕上），单测测不出。

对照组（摘掉校验跑同一脚本）的现场：空壳落进 checkpoint → 无名工具走到
``approval.request``（用户收到一张没有名字的审批卡）→ 本轮永挂；之后每轮再撞
``ToolMessage(tool_call_id=None)`` 的 pydantic 校验，会话报废。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult

from lumi.gateway.bridge import AgentBridge
from lumi.gateway.bridge.core import EventKind


class ScriptedLLM(BaseChatModel):
    """按 script 逐次流式回放；每步是 chunk 列表，交 LangChain 自行聚合。"""

    script: list[list[AIMessageChunk]] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self  # 真链会调它；工具绑定与本用例无关

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        for chunk in step:
            yield ChatGenerationChunk(message=chunk)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError("只走流式")


def _text(s: str) -> AIMessageChunk:
    return AIMessageChunk(content=s)


def _shell() -> AIMessageChunk:
    """缺 id / name 的 tool_call 残片：聚合后即 {'name': '', 'id': None}。"""
    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": None, "args": '{"description": "b"}', "id": None, "index": 0}
        ],
    )


async def _no_tools(self, wait_mcp: bool = False) -> list:
    return []  # 不拉起项目 MCP：与本用例无关且会阻塞


@pytest.fixture(autouse=True)
def _isolated(isolated_config):
    """真 initialize 走 sqlite checkpointer：不隔离会写进 ~/.lumi，侧栏冒出测试会话。"""


async def _run(script: list[list[AIMessageChunk]], tmp_path):
    bridge = AgentBridge()
    with (
        patch("lumi.models.chain.create_llm", return_value=ScriptedLLM(script=script)),
        patch.object(AgentBridge, "_build_tools", new=_no_tools),
    ):
        await bridge.initialize(project_dir=str(tmp_path))
        events = [evt async for evt in bridge.stream_response("你好")]
    state = await bridge._agent.graph.aget_state(bridge._config)
    return [e.kind.value for e in events], events, state.values["messages"]


@pytest.mark.asyncio
async def test_malformed_response_never_reaches_checkpoint(tmp_path):
    kinds, events, messages = await _run(
        [
            [_text("'by 能, "), _text("the the"), _shell()],
            [_text("重试后的正常回答")],
        ],
        tmp_path,
    )
    assert kinds.count("message.retry") == 1

    ai = [m for m in messages if isinstance(m, AIMessage)]
    assert [m.content for m in ai] == ["重试后的正常回答"]  # 乱码那条没落库
    assert ai[-1].tool_calls == []

    i = kinds.index("message.retry")
    before = "".join(e.text for e in events[:i] if e.kind == EventKind.MESSAGE_DELTA)
    after = "".join(e.text for e in events[i:] if e.kind == EventKind.MESSAGE_DELTA)
    assert before == "'by 能, the the"  # 乱码确实已经流到前端（所以必须回滚）
    assert after == "重试后的正常回答"  # 重试的正文从头流出


@pytest.mark.asyncio
async def test_retry_event_arrives_after_that_calls_complete(tmp_path):
    """回滚边界必须来自 message.start：complete 先到，「streaming 中」已被清掉。"""
    kinds, _, _ = await _run(
        [[_text("乱码"), _shell()], [_text("正常")]],
        tmp_path,
    )
    i = kinds.index("message.retry")
    assert "message.complete" in kinds[:i]
    assert kinds[i + 1] == "message.start"


@pytest.mark.asyncio
async def test_still_malformed_strips_shells_and_finishes_turn(tmp_path):
    """两次都畸形：剔壳兜底，轮次正常收尾，checkpoint 里没有空壳。"""
    bad = [_text("乱码"), _shell()]
    kinds, _, messages = await _run([bad, list(bad)], tmp_path)
    assert kinds.count("message.retry") == 1  # 兜底不再发第二次
    assert "error" not in kinds
    assert kinds[-1] == "turn.complete"
    assert all(m.tool_calls == [] for m in messages if isinstance(m, AIMessage))


@pytest.mark.asyncio
async def test_clean_response_streams_without_retry(tmp_path):
    kinds, _, messages = await _run([[_text("正常回答")]], tmp_path)
    assert "message.retry" not in kinds
    assert [m.content for m in messages if isinstance(m, AIMessage)] == ["正常回答"]

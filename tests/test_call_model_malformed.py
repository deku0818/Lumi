"""CallModel 畸形 tool_calls 重试：流式聚合吐出缺 id / name 的空壳时不落库。

客户现场：qwen 一轮输出 3 个 id=None 的空壳 tool_call 落进 checkpoint，之后每轮
_recover_stale_state 补配对 ToolMessage(tool_call_id=None) 撞 pydantic 校验，会话永久
不可用。锁：
- 首次畸形 → 发 retry 事件 + 重调一次，第二次正常则原样返回
- 两次都畸形 → 剔掉空壳兜底返回，不抛错、不再发事件
- 正常响应零开销：只调一次、不发事件
- bridge 把 custom event 翻成 message.retry；飞书 _reset 回滚本次调用的正文

注意事件次序：畸形那次调用的 message.complete 先于 message.retry 到达（端到端实测），
故两端都不能靠「streaming 中」反推该丢什么，边界由 message.start 记下。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conftest import tool_loop_history
from langchain_core.messages import AIMessage

from lumi.agents.core import nodes
from lumi.agents.core.node_helpers.messages import is_malformed_tool_call
from lumi.agents.core.nodes import LUMI_MODEL_RETRY_EVENT
from lumi.gateway.channels.feishu.streaming import FeishuStreaming, _status_line

_RUNTIME = SimpleNamespace(
    context=SimpleNamespace(
        tools=[],
        system_prompt="SYS",
        model_name="fake-model",
        provider="",
        effort=None,
        memory_enabled=True,
    )
)
_GOOD = {"name": "read", "args": {"path": "x"}, "id": "call_1"}
_SHELL = {"name": "", "args": {"description": "b"}, "id": None}


def _bad_response(mid: str) -> AIMessage:
    return AIMessage(content="'by 能, the the", id=mid, tool_calls=[_GOOD, _SHELL])


async def _run(chain):
    dispatch = AsyncMock()
    with (
        patch.object(nodes, "tool_call_chain", return_value=chain),
        patch.object(
            nodes,
            "get_config",
            return_value=SimpleNamespace(
                config=SimpleNamespace(agents=SimpleNamespace(max_tokens=None))
            ),
        ),
        patch.object(nodes, "detect_protocol", return_value="openai"),
        patch.object(nodes, "adispatch_custom_event", dispatch),
    ):
        result = await nodes.call_model({"messages": tool_loop_history()}, _RUNTIME)
    return result, dispatch


def test_malformed_detects_missing_id_or_name():
    assert is_malformed_tool_call(_SHELL)
    assert is_malformed_tool_call({"name": "x", "args": {}, "id": ""})
    assert not is_malformed_tool_call(_GOOD)


async def test_retry_once_then_accept_clean_response():
    ok = AIMessage(content="ok", id="r2", tool_calls=[_GOOD])
    chain = SimpleNamespace(ainvoke=AsyncMock(side_effect=[_bad_response("r1"), ok]))
    result, dispatch = await _run(chain)
    assert chain.ainvoke.await_count == 2
    dispatch.assert_awaited_once_with(LUMI_MODEL_RETRY_EVENT, {})
    assert result["messages"] == [ok]


async def test_still_malformed_after_retry_strips_shells():
    chain = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=[_bad_response("r1"), _bad_response("r2")])
    )
    result, dispatch = await _run(chain)
    assert chain.ainvoke.await_count == 2
    assert dispatch.await_count == 1  # 兜底不再发事件
    (msg,) = result["messages"]
    assert msg.id == "r2"
    assert [tc["id"] for tc in msg.tool_calls] == ["call_1"]  # 空壳剔除，合法调用保留


async def test_clean_response_no_retry_no_event():
    ok = AIMessage(content="ok", id="r1", tool_calls=[_GOOD])
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=ok))
    result, dispatch = await _run(chain)
    assert chain.ainvoke.await_count == 1
    dispatch.assert_not_awaited()
    assert result["messages"] == [ok]


async def test_feishu_reset_clears_buffer_and_shows_retry_status():
    streaming = FeishuStreaming(SimpleNamespace(client=object()))
    pushed: list[str] = []

    async def _push(buf, card_id, text):
        pushed.append(text)
        return True, 0

    streaming._push_update = _push  # type: ignore[method-assign]
    buf = streaming._new_buf("chat")
    buf.card_id = "card"
    buf.text = "'by 能, the the latest logs"
    streaming.bufs["chat"] = buf

    await streaming.reset("chat", None)
    await buf.queue.drain()

    assert buf.text == ""
    assert pushed == [""]  # 正文清空：乱码不留在屏幕上
    assert buf.retrying and buf.busy
    assert "重新生成中" in _status_line(buf)  # 状态行改显重试文案

    await streaming.append("chat", "重试的正文", None)  # 正文一到即让位
    assert not buf.retrying and not buf.busy


async def test_feishu_reset_keeps_earlier_iteration_text():
    """同一轮里更早迭代（已落库）的正文不被回滚：只丢畸形那次调用流出的部分。"""
    streaming = FeishuStreaming(SimpleNamespace(client=object()))
    streaming._push_update = AsyncMock(return_value=(True, 0))  # type: ignore[method-assign]
    buf = streaming._new_buf("chat")
    buf.card_id = "card"
    streaming.bufs["chat"] = buf

    streaming.mark("chat")  # 第一次模型调用
    await streaming.append("chat", "我来查一下", None)
    await streaming.tool_activity("chat", "start", "read", None)
    streaming.mark("chat")  # 第二次调用（畸形）
    await streaming.append("chat", "'by 能, the the", None)
    await streaming.reset("chat", None)

    assert buf.text == "我来查一下"  # 只回滚第二次调用的乱码


async def test_feishu_retry_status_yields_to_tool_activity():
    """重试的响应直接调工具（无正文）时，状态行必须让位给工具行而非卡在重试文案。"""
    streaming = FeishuStreaming(SimpleNamespace(client=object()))
    streaming._push_update = AsyncMock(return_value=(True, 0))  # type: ignore[method-assign]
    buf = streaming._new_buf("chat")
    buf.card_id = "card"
    streaming.bufs["chat"] = buf

    await streaming.reset("chat", None)
    assert buf.retrying
    await streaming.tool_activity("chat", "start", "read", None)
    assert not buf.retrying
    assert "重新生成中" not in _status_line(buf)

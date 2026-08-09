"""时间旅行截断（rewind_before_message）与重发消息重建的行为测试。

用真实 LumiAgent 图 + MemorySaver 锁四件事：按 message_id 截断到位、
新末条的 ctx_digest marker 被剥（不剥会让 context_inject 误判漏注上下文）、
todos 随截断清空（不清会把已删未来的任务列表带进重答轮）、目标不存在时不动 state。
重建测试锁「剥注入前缀 + 重挂附件标签」——不剥会让重发轮与新注入叠加。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.meta_message import (
    CTX_DIGEST_KEY,
    declared_file_paths,
    injected_prefix,
    strip_injected_prefix,
)
from lumi.agents.core.node_helpers.messages import inject_text_into_message
from lumi.gateway.bridge.core import AgentBridge
from lumi.sessions.message_text import extract_text_content
from lumi.utils.constants import LUMI_META_KEY
from tests.gateway.toy_graph import bridge_with


def _bridge(thread_id: str = "t-rewind") -> AgentBridge:
    """真实 AgentBridge + 真实图（截断走 aupdate_state，玩具图测不出条件边约束）。"""
    return bridge_with(
        {"configurable": {"thread_id": thread_id}},
        LumiAgent(checkpointer=MemorySaver()).graph,
    )


async def _seed(bridge, messages: list, todos: list | None = None) -> list:
    update: dict = {"messages": messages}
    if todos is not None:
        update["todos"] = todos
    await bridge.graph.aupdate_state(bridge._config, update, as_node="OfflineFlush")
    return await bridge.snapshot_messages()


@pytest.mark.asyncio
async def test_rewind_truncates_from_target_inclusive():
    bridge = _bridge()
    seeded = await _seed(
        bridge,
        [HumanMessage("h1"), AIMessage("a1"), HumanMessage("h2"), AIMessage("a2")],
    )
    removed = await bridge.rewind_before_message(seeded[2].id)
    assert removed is not None and removed.content == "h2"
    remaining = await bridge.snapshot_messages()
    assert [m.content for m in remaining] == ["h1", "a1"]


@pytest.mark.asyncio
async def test_rewind_strips_ctx_digest_on_new_tail():
    bridge = _bridge()
    seeded = await _seed(
        bridge,
        [
            HumanMessage("h1"),
            AIMessage("a1", additional_kwargs={CTX_DIGEST_KEY: {"env": "x"}}),
            HumanMessage("h2"),
        ],
    )
    await bridge.rewind_before_message(seeded[2].id)
    remaining = await bridge.snapshot_messages()
    tail = remaining[-1]
    assert tail.content == "a1"
    assert tail.id == seeded[1].id  # 同 id 原地重挂，位置与身份不变
    assert CTX_DIGEST_KEY not in tail.additional_kwargs


@pytest.mark.asyncio
async def test_rewind_clears_todos():
    bridge = _bridge()
    seeded = await _seed(
        bridge,
        [HumanMessage("h1"), AIMessage("a1"), HumanMessage("h2")],
        todos=[{"content": "幽灵任务", "status": "pending"}],
    )
    await bridge.rewind_before_message(seeded[2].id)
    state = await bridge.graph.aget_state(bridge._config)
    assert state.values.get("todos") == []


@pytest.mark.asyncio
async def test_rewind_unknown_id_is_noop():
    bridge = _bridge()
    await _seed(bridge, [HumanMessage("h1"), AIMessage("a1")])
    assert await bridge.rewind_before_message("no-such-id") is None
    assert [m.content for m in await bridge.snapshot_messages()] == ["h1", "a1"]


def _rebuild(msg):
    """stream_regenerate 的重建步（与其保持同构：剥注入前缀 → 按声明重建）。"""
    return AgentBridge._build_user_message(
        strip_injected_prefix(msg),
        msg.additional_kwargs.get(LUMI_META_KEY),
        declared_file_paths(msg),
    )


def test_rebuild_strips_injected_prefix_and_reattaches_files():
    """重建剥掉全部注入前缀（上下文块 + 附件标签），按声明路径重挂标签。"""
    msg = AgentBridge._build_user_message("原话", None, ["/tmp/a.txt"])
    # 模拟原轮的上下文注入（前置块 + 计数）
    msg = inject_text_into_message(
        msg, "<system-reminder>旧环境上下文</system-reminder>"
    )
    assert injected_prefix(msg) == 2  # 附件标签块 + 上下文块

    rebuilt = _rebuild(msg)
    text = extract_text_content(rebuilt.content)
    assert "旧环境上下文" not in text  # 旧注入不叠加
    assert "/tmp/a.txt" in text  # 附件标签按声明重挂
    assert "原话" in text
    assert injected_prefix(rebuilt) == 1  # 只剩重挂的附件标签块
    assert rebuilt.id != msg.id  # 换新 id 重挂
    # 显示声明原样保留（ts / files），且不因重挂多出重复的附件条目
    items = rebuilt.additional_kwargs[LUMI_META_KEY]["items"]
    assert len(items) == 1
    assert items[0]["files"] == [{"path": "/tmp/a.txt", "name": "a.txt"}]
    assert items[0]["ts"] == msg.additional_kwargs[LUMI_META_KEY]["items"][0]["ts"]


def test_rebuild_is_idempotent():
    """重建的重建仍等价——反复重新生成不会累积注入块或附件条目。"""
    once = _rebuild(AgentBridge._build_user_message("原话", None, ["/tmp/a.txt"]))
    twice = _rebuild(once)
    assert extract_text_content(twice.content) == extract_text_content(once.content)
    assert injected_prefix(twice) == injected_prefix(once) == 1
    assert (
        twice.additional_kwargs[LUMI_META_KEY]["items"]
        == (once.additional_kwargs[LUMI_META_KEY]["items"])
    )


def test_rebuild_plain_text_message_roundtrip():
    """无附件无注入的纯文本消息重建后内容不变。"""
    rebuilt = _rebuild(AgentBridge._build_user_message("你好", None, []))
    assert extract_text_content(rebuilt.content) == "你好"
    assert injected_prefix(rebuilt) == 0

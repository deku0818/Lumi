"""preprocess_messages agent 注入逻辑单元测试

镜像 skill 注入：验证 agent 列表注入在预处理之后执行、仅最后一条 HumanMessage
被注入、未变更/空列表不注入、summary 后摘要消息带上当前 agent 列表。
注入是否发生由「当前 agent 工具集是否含 agent 工具」门控（见 _agent_tool_available）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from lumi.agents.tools.loader import AgentConfig

_TEST_AGENTS = [
    AgentConfig(
        name="test_agent",
        description="测试代理",
        system_prompt="你是一个测试代理",
    ),
]


@pytest.fixture(autouse=True)
def _mute_skill_injection():
    """本文件只验证 agent 注入；让 skill 检测器返回空、记忆注入 no-op，
    避免其 system-reminder 干扰断言（记忆注入会读真实 LUMI.md / ~/.lumi）。"""
    with (
        patch("lumi.agents.core.nodes.SkillChangeDetector") as mock_cls,
        patch(
            "lumi.agents.core.nodes.inject_memory_context_into_message",
            side_effect=lambda msg, *a, **k: msg,
        ),
    ):
        mock_cls.get_instance.return_value.check.return_value = ([], False)
        yield


def _runtime(has_agent: bool = True) -> SimpleNamespace:
    """构造带工具集的假 runtime；has_agent 决定工具集是否含 agent 工具。"""
    tools = [SimpleNamespace(name="bash"), SimpleNamespace(name="read")]
    if has_agent:
        tools.append(SimpleNamespace(name="agent"))
    return SimpleNamespace(context=SimpleNamespace(tools=tools, memory_enabled=False))


def _make_state(messages: list) -> dict:
    return {"messages": messages}


def _has_agent_reminder(message: HumanMessage) -> bool:
    content = message.content
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and "可用于 agent 工具" in b.get("text", "")
        for b in content
    )


@patch(
    "lumi.agents.core.nodes.inject_system_info_into_message", side_effect=lambda m: m
)
@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_agent_injection_after_preprocessing(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
    mock_sys_info: MagicMock,
) -> None:
    """agent 变更时，注入逻辑生成 RemoveMessage + 带 system-reminder 的新 HumanMessage。"""
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        True,
    )

    state = _make_state(messages=[HumanMessage(content="你好", id="msg1")])

    result = await preprocess_messages(state, _runtime())
    msgs = result["messages"]

    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]

    assert len(remove_msgs) == 1 and remove_msgs[0].id == "msg1"
    assert len(human_msgs) == 1
    assert _has_agent_reminder(human_msgs[0]), "新消息应包含 agent system-reminder"
    mock_cleanup.assert_called_once()


@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_only_last_human_message_injected(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """多条 HumanMessage 时，仅最后一条被注入。"""
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        True,
    )

    state = _make_state(
        messages=[
            HumanMessage(content="第一条", id="msg1"),
            AIMessage(content="回复", id="ai1"),
            HumanMessage(content="最后一条", id="msg3"),
        ],
    )

    result = await preprocess_messages(state, _runtime())
    msgs = result["messages"]

    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]

    assert len(remove_msgs) == 1 and remove_msgs[0].id == "msg3"
    assert len(human_msgs) == 1 and _has_agent_reminder(human_msgs[0])


@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_no_injection_when_not_changed(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """agent 未变更（changed=False）时不注入。多条消息避开首条系统信息注入。"""
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        False,
    )

    state = _make_state(
        messages=[
            HumanMessage(content="第一条", id="msg0"),
            AIMessage(content="回复", id="ai0"),
            HumanMessage(content="你好", id="msg1"),
        ],
    )

    result = await preprocess_messages(state, _runtime())
    msgs = result["messages"]

    assert [m for m in msgs if isinstance(m, RemoveMessage)] == []
    assert [m for m in msgs if isinstance(m, HumanMessage)] == []


@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_no_injection_when_agents_empty(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """agent 变更但列表为空时不注入。"""
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = ([], True)

    state = _make_state(
        messages=[
            HumanMessage(content="第一条", id="msg0"),
            AIMessage(content="回复", id="ai0"),
            HumanMessage(content="你好", id="msg1"),
        ],
    )

    result = await preprocess_messages(state, _runtime())
    msgs = result["messages"]

    assert [m for m in msgs if isinstance(m, RemoveMessage)] == []
    assert [m for m in msgs if isinstance(m, HumanMessage)] == []


@patch("lumi.agents.core.nodes.AgentChangeDetector")
async def test_summary_injects_agents_into_summary_message(
    mock_detector_cls: MagicMock,
    run_summarizer,
) -> None:
    """串行压缩后，摘要消息应带上当前 agent 列表的 system-reminder。"""
    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        False,
    )

    state = _make_state(
        messages=[
            HumanMessage(content="早期消息", id="msg1"),
            AIMessage(content="回复", id="ai1"),
            HumanMessage(content="最新消息", id="msg2"),
        ]
    )

    result = await run_summarizer(state, _runtime(), "一段对话摘要", "t-agent")
    human_msgs = [m for m in result["messages"] if isinstance(m, HumanMessage)]

    assert len(human_msgs) == 1
    assert _has_agent_reminder(human_msgs[0]), "摘要消息应包含 agent system-reminder"


@patch(
    "lumi.agents.core.nodes.inject_system_info_into_message", side_effect=lambda m: m
)
@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_first_message_injects_even_when_not_changed(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
    mock_sys_info: MagicMock,
) -> None:
    """全新历史的可委派子代理：即使 changed=False，首条消息也应注入 agent 列表。

    复现 finding 2：detector 单例的 changed=True 早被主 agent 消费，子代理 check()
    恒返 False；若仅按 changed 门控，子代理永远拿不到可用代理名。
    """
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        False,  # changed=False
    )

    # 工具集含 agent（可委派）+ 单条消息（首条）
    state = _make_state(messages=[HumanMessage(content="干活", id="m1")])

    result = await preprocess_messages(state, _runtime(has_agent=True))
    human_msgs = [m for m in result["messages"] if isinstance(m, HumanMessage)]

    assert len(human_msgs) == 1
    assert _has_agent_reminder(human_msgs[0]), "可委派子代理首条消息应注入 agent 列表"


@patch("lumi.agents.core.nodes.AgentChangeDetector")
@patch("lumi.agents.core.nodes.cleanup_incomplete_tool_calls", return_value=[])
async def test_no_agent_tool_not_injected_even_when_changed(
    mock_cleanup: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """工具集不含 agent 工具（达上限的叶子代理 / tools 白名单排除 agent）即使
    changed=True 也不应被注入 agent 列表。

    复现 finding 1：注入门控以实际工具集为准，而非仅看 depth。
    用多条消息避开首条系统信息注入。
    """
    from lumi.agents.core.nodes import preprocess_messages

    mock_detector_cls.get_instance.return_value.check.return_value = (
        _TEST_AGENTS,
        True,  # 即便变更
    )

    state = _make_state(
        messages=[
            HumanMessage(content="第一条", id="m0"),
            AIMessage(content="回复", id="a0"),
            HumanMessage(content="你好", id="m1"),
        ],
    )

    result = await preprocess_messages(state, _runtime(has_agent=False))
    msgs = result["messages"]

    assert [m for m in msgs if isinstance(m, RemoveMessage)] == []
    assert [m for m in msgs if isinstance(m, HumanMessage)] == []

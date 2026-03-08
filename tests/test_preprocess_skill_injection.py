"""preprocess_messages 技能注入逻辑单元测试

验证技能注入在现有预处理步骤之后执行，
仅最后一条 HumanMessage 被注入（Property 6）。

Requirements: 3.1, 3.2, 3.3
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from lumi.agents.tools.config import SkillConfig

# 测试用的技能配置
_TEST_SKILLS = [
    SkillConfig(name="test_skill", description="测试技能", prompt="测试提示词"),
]


def _make_state(
    messages: list,
    summary: dict | None = None,
) -> dict:
    """构建最小化的 LumiAgentState 字典。"""
    return {
        "messages": messages,
        "summary": summary or {},
    }


# --- 公共 mock 装饰器 ---
# mock 掉 preprocess_messages 中依赖的外部函数，隔离技能注入逻辑


def _patch_preprocessing():
    """返回三层 patch 装饰器，mock 掉清理和卸载步骤。"""
    return (
        patch("lumi.agents.core.node.cleanup_incomplete_tool_calls", return_value=[]),
        patch("lumi.agents.core.node.offload_tool_result", return_value=[]),
        patch("lumi.agents.core.node.SkillChangeDetector"),
    )


# --- 测试 1: 技能注入在预处理之后执行 ---


@patch("lumi.agents.core.node.SkillChangeDetector")
@patch("lumi.agents.core.node.offload_tool_result", return_value=[])
@patch("lumi.agents.core.node.cleanup_incomplete_tool_calls", return_value=[])
async def test_skill_injection_after_preprocessing(
    mock_cleanup: MagicMock,
    mock_offload: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """验证技能变更时，注入逻辑生成 RemoveMessage + 新 HumanMessage。

    Validates: Requirements 3.1, 3.3
    """
    from lumi.agents.core.node import preprocess_messages

    # 配置 mock detector
    mock_detector = MagicMock()
    mock_detector.check.return_value = (_TEST_SKILLS, True)
    mock_detector_cls.get_instance.return_value = mock_detector

    state = _make_state(
        messages=[HumanMessage(content="你好", id="msg1")],
    )

    result = await preprocess_messages(state)
    msgs = result["messages"]

    # 应包含 RemoveMessage（删除原消息）和新 HumanMessage（带 system-reminder）
    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]

    assert len(remove_msgs) == 1, f"应有 1 条 RemoveMessage，实际 {len(remove_msgs)}"
    assert remove_msgs[0].id == "msg1"

    assert len(human_msgs) == 1, f"应有 1 条新 HumanMessage，实际 {len(human_msgs)}"

    # 新消息应包含 system-reminder
    new_content = human_msgs[0].content
    assert isinstance(new_content, list)
    reminder_blocks = [
        b
        for b in new_content
        if isinstance(b, dict) and "<system-reminder>" in b.get("text", "")
    ]
    assert len(reminder_blocks) == 1, "新消息应包含恰好 1 个 system-reminder block"

    # 确认预处理步骤被调用
    mock_cleanup.assert_called_once()
    mock_offload.assert_called_once()


# --- 测试 2: 仅最后一条 HumanMessage 被注入（Property 6）---


@patch("lumi.agents.core.node.SkillChangeDetector")
@patch("lumi.agents.core.node.offload_tool_result", return_value=[])
@patch("lumi.agents.core.node.cleanup_incomplete_tool_calls", return_value=[])
async def test_only_last_human_message_injected(
    mock_cleanup: MagicMock,
    mock_offload: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """多条 HumanMessage 时，仅最后一条被注入。

    **Validates: Requirements 3.2** (Property 6)
    """
    from lumi.agents.core.node import preprocess_messages

    mock_detector = MagicMock()
    mock_detector.check.return_value = (_TEST_SKILLS, True)
    mock_detector_cls.get_instance.return_value = mock_detector

    state = _make_state(
        messages=[
            HumanMessage(content="第一条消息", id="msg1"),
            AIMessage(content="AI 回复", id="ai1"),
            HumanMessage(content="第二条消息", id="msg2"),
            AIMessage(content="AI 回复2", id="ai2"),
            HumanMessage(content="最后一条消息", id="msg3"),
        ],
    )

    result = await preprocess_messages(state)
    msgs = result["messages"]

    # RemoveMessage 应仅针对最后一条 HumanMessage (msg3)
    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    assert len(remove_msgs) == 1
    assert remove_msgs[0].id == "msg3", (
        f"应删除最后一条 HumanMessage (msg3)，实际删除 {remove_msgs[0].id}"
    )

    # 新注入的消息应包含 system-reminder
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]
    assert len(human_msgs) == 1
    new_content = human_msgs[0].content
    assert isinstance(new_content, list)
    has_reminder = any(
        isinstance(b, dict) and "<system-reminder>" in b.get("text", "")
        for b in new_content
    )
    assert has_reminder, "注入的新消息应包含 system-reminder"


# --- 测试 4: 技能未变更时不注入 ---


@patch("lumi.agents.core.node.SkillChangeDetector")
@patch("lumi.agents.core.node.offload_tool_result", return_value=[])
@patch("lumi.agents.core.node.cleanup_incomplete_tool_calls", return_value=[])
async def test_no_injection_when_not_changed(
    mock_cleanup: MagicMock,
    mock_offload: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """技能未发生变更时（changed=False），不应注入。

    Validates: Requirements 3.3
    """
    from lumi.agents.core.node import preprocess_messages

    mock_detector = MagicMock()
    mock_detector.check.return_value = (_TEST_SKILLS, False)
    mock_detector_cls.get_instance.return_value = mock_detector

    state = _make_state(
        messages=[HumanMessage(content="你好", id="msg1")],
    )

    result = await preprocess_messages(state)
    msgs = result["messages"]

    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]

    assert len(remove_msgs) == 0, "未变更时不应生成 RemoveMessage"
    assert len(human_msgs) == 0, "未变更时不应生成新 HumanMessage"


# --- 测试 5: 技能列表为空时不注入 ---


@patch("lumi.agents.core.node.SkillChangeDetector")
@patch("lumi.agents.core.node.offload_tool_result", return_value=[])
@patch("lumi.agents.core.node.cleanup_incomplete_tool_calls", return_value=[])
async def test_no_injection_when_skills_empty(
    mock_cleanup: MagicMock,
    mock_offload: MagicMock,
    mock_detector_cls: MagicMock,
) -> None:
    """技能变更但列表为空时，不应注入。

    Validates: Requirements 3.3
    """
    from lumi.agents.core.node import preprocess_messages

    mock_detector = MagicMock()
    mock_detector.check.return_value = ([], True)
    mock_detector_cls.get_instance.return_value = mock_detector

    state = _make_state(
        messages=[HumanMessage(content="你好", id="msg1")],
    )

    result = await preprocess_messages(state)
    msgs = result["messages"]

    remove_msgs = [m for m in msgs if isinstance(m, RemoveMessage)]
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]

    assert len(remove_msgs) == 0, "技能为空时不应生成 RemoveMessage"
    assert len(human_msgs) == 0, "技能为空时不应生成新 HumanMessage"


# --- 测试: summary 后摘要消息注入技能列表 ---


@patch("lumi.agents.core.node.SkillChangeDetector")
async def test_summary_injects_skills_into_summary_message(
    mock_detector_cls: MagicMock,
) -> None:
    """summary 替换后，摘要消息应包含当前技能列表的 system-reminder。

    场景：聊天期间修改了 skill，触发 summary 后，
    摘要消息需要带上最新技能列表，否则 LLM 会丢失技能感知。
    """
    from lumi.agents.core.node import preprocess_messages

    mock_detector = MagicMock()
    mock_detector.check.return_value = (_TEST_SKILLS, False)
    mock_detector_cls.get_instance.return_value = mock_detector

    state = _make_state(
        messages=[
            HumanMessage(content="早期消息", id="msg1"),
            AIMessage(content="AI 回复", id="ai1"),
            HumanMessage(content="最新消息", id="msg2"),
        ],
        summary={
            "summarized_ids": ["msg1", "ai1"],
            "summary_text": "用户和 AI 进行了一段对话",
        },
    )

    result = await preprocess_messages(state)
    msgs = result["messages"]

    # 找到摘要消息（HumanMessage）
    human_msgs = [m for m in msgs if isinstance(m, HumanMessage)]
    assert len(human_msgs) == 1, f"应有 1 条摘要 HumanMessage，实际 {len(human_msgs)}"

    summary_msg = human_msgs[0]
    # 摘要消息应包含 system-reminder
    content = summary_msg.content
    assert isinstance(content, list), "注入后 content 应为列表"
    has_reminder = any(
        isinstance(b, dict) and "<system-reminder>" in b.get("text", "")
        for b in content
    )
    assert has_reminder, "摘要消息应包含 system-reminder"

    # 摘要内容也应保留
    has_summary = any(
        isinstance(b, dict) and "历史对话摘要" in b.get("text", "") for b in content
    )
    assert has_summary, "摘要消息应保留摘要内容"

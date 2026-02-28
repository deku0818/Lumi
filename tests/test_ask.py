"""Ask 工具测试"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from langgraph.types import Command
from lumi.agents.tools.providers.ask import Question, QuestionOption, ask


def _make_tool_call(args, tool_call_id="tc_test"):
    """构造完整的 ToolCall 格式（InjectedToolCallId 要求）"""
    return {"args": args, "name": "ask", "type": "tool_call", "id": tool_call_id}


def test_question_option_model():
    opt = QuestionOption(label="Yes", description="Confirm action")
    assert opt.label == "Yes"
    assert opt.description == "Confirm action"


def test_question_model_options_count():
    opts = [QuestionOption(label=f"O{i}", description=f"D{i}") for i in range(2)]
    q = Question(question="Pick?", header="Choice", options=opts, multiSelect=False)
    assert len(q.options) == 2

    # 少于 2 个选项应报错
    with pytest.raises(ValidationError):
        Question(
            question="Pick?",
            header="Choice",
            options=[QuestionOption(label="Only", description="One")],
            multiSelect=False,
        )

    # 超过 4 个选项应报错
    with pytest.raises(ValidationError):
        Question(
            question="Pick?",
            header="Choice",
            options=[
                QuestionOption(label=f"O{i}", description=f"D{i}") for i in range(5)
            ],
            multiSelect=False,
        )


def test_ask_builds_interrupt_data():
    opts = [
        QuestionOption(label="A", description="Option A"),
        QuestionOption(label="B", description="Option B"),
    ]
    q = Question(question="Which?", header="Test", options=opts, multiSelect=False)

    with patch("lumi.agents.tools.providers.ask.interrupt") as mock_interrupt:
        mock_interrupt.return_value = "User chose A"
        ask.invoke(_make_tool_call({"questions": [q]}, "tc_789"))

    mock_interrupt.assert_called_once()
    call_data = mock_interrupt.call_args[0][0]
    assert call_data["type"] == "ask"
    assert call_data["tool_call_id"] == "tc_789"
    assert len(call_data["questions"]) == 1
    # 选项应包含原始 2 个 + 自定义输入选项
    assert len(call_data["questions"][0]["options"]) == 3


def test_ask_returns_command():
    opts = [
        QuestionOption(label="X", description="DX"),
        QuestionOption(label="Y", description="DY"),
    ]
    q = Question(question="Pick?", header="H", options=opts, multiSelect=False)

    with patch("lumi.agents.tools.providers.ask.interrupt") as mock_interrupt:
        mock_interrupt.return_value = "Selected X"
        result = ask.invoke(_make_tool_call({"questions": [q]}, "tc_abc"))

    assert isinstance(result, Command)
    msg = result.update["messages"][0].content
    assert "Selected X" in msg

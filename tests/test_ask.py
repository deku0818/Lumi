"""Ask 工具测试"""

from types import SimpleNamespace

import pytest
from langgraph.types import Command
from pydantic import ValidationError

from lumi.agents.tools.providers.ask import (
    ASK_CANCELLED,
    Question,
    QuestionOption,
    ask,
)


class _FakeBroker:
    """捕获 ask 经 broker.request 发出的 payload，并回应预设值。"""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def request(self, payload: dict, reject_value):
        self.calls.append(payload)
        return self._response


def _runtime_with(response):
    """构造带 fake broker 的注入 runtime（ask 经 runtime.context.approval_broker 审批）。"""
    return SimpleNamespace(
        context=SimpleNamespace(approval_broker=_FakeBroker(response))
    )


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


async def test_ask_builds_approval_payload():
    opts = [
        QuestionOption(label="A", description="Option A"),
        QuestionOption(label="B", description="Option B"),
    ]
    q = Question(question="Which?", header="Test", options=opts, multiSelect=False)

    runtime = _runtime_with("User chose A")
    await ask.coroutine(questions=[q], tool_call_id="tc_789", runtime=runtime)

    calls = runtime.context.approval_broker.calls
    assert len(calls) == 1
    payload = calls[0]
    assert payload["type"] == "ask"
    assert payload["tool_call_id"] == "tc_789"
    assert len(payload["questions"]) == 1
    # 选项应包含原始 2 个 + 自定义输入选项
    assert len(payload["questions"][0]["options"]) == 3


async def test_ask_returns_command():
    opts = [
        QuestionOption(label="X", description="DX"),
        QuestionOption(label="Y", description="DY"),
    ]
    q = Question(question="Pick?", header="H", options=opts, multiSelect=False)

    runtime = _runtime_with("Selected X")
    result = await ask.coroutine(questions=[q], tool_call_id="tc_abc", runtime=runtime)

    assert isinstance(result, Command)
    msg = result.update["messages"][0].content
    assert "Selected X" in msg


async def test_ask_no_broker_headless_proceeds():
    """无审批通道（headless：cron / workflow，approval_broker=None）：返回提示让模型继续，不崩溃。"""
    opts = [
        QuestionOption(label="X", description="DX"),
        QuestionOption(label="Y", description="DY"),
    ]
    q = Question(question="Pick?", header="H", options=opts, multiSelect=False)

    runtime = SimpleNamespace(context=SimpleNamespace(approval_broker=None))
    result = await ask.coroutine(questions=[q], tool_call_id="tc_h", runtime=runtime)

    assert isinstance(result, Command)
    # 不中断（无 tool_cancelled），让自治 agent 继续
    assert "tool_cancelled" not in result.update
    assert result.update["messages"][0].tool_call_id == "tc_h"


async def test_ask_cancelled_sets_flag():
    opts = [
        QuestionOption(label="X", description="DX"),
        QuestionOption(label="Y", description="DY"),
    ]
    q = Question(question="Pick?", header="H", options=opts, multiSelect=False)

    runtime = _runtime_with(ASK_CANCELLED)
    result = await ask.coroutine(questions=[q], tool_call_id="tc_x", runtime=runtime)

    assert isinstance(result, Command)
    assert result.update["tool_cancelled"] is True

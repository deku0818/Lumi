"""Todo 工具测试"""

import pytest
from pydantic import ValidationError

from langgraph.types import Command
from lumi.agents.tools.providers.todo import Todo, todos


def _make_tool_call(args, tool_call_id="tc_test"):
    """构造完整的 ToolCall 格式（InjectedToolCallId 要求）"""
    return {"args": args, "name": "todos", "type": "tool_call", "id": tool_call_id}


def test_todo_model_validation():
    t = Todo(content="Run tests", status="pending")
    assert t.content == "Run tests"
    assert t.status == "pending"


def test_todo_model_invalid_status():
    with pytest.raises(ValidationError):
        Todo(content="Bad", status="unknown")


def test_todos_returns_command():
    items = [
        Todo(content="task1", status="pending"),
        Todo(content="task2", status="completed"),
    ]
    result = todos.invoke(_make_tool_call({"todos": items}, "tc_123"))
    assert isinstance(result, Command)


def test_todos_status_summary():
    items = [
        Todo(content="a", status="pending"),
        Todo(content="b", status="in_progress"),
        Todo(content="c", status="completed"),
        Todo(content="d", status="completed"),
    ]
    result = todos.invoke(_make_tool_call({"todos": items}, "tc_456"))
    assert isinstance(result, Command)
    msg = result.update["messages"][0].content
    assert "待处理: 1" in msg
    assert "进行中: 1" in msg
    assert "已完成: 2" in msg
    assert "4 项" in msg

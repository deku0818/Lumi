"""旧 todo 数据读取兼容测试。"""

import pytest
from pydantic import ValidationError

from lumi.agents.tools.providers.todo import Todo


def test_todo_model_validation():
    t = Todo(content="Run tests", status="pending")
    assert t.content == "Run tests"
    assert t.status == "pending"


def test_todo_model_invalid_status():
    with pytest.raises(ValidationError):
        Todo(content="Bad", status="unknown")

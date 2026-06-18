"""消息可见性判定测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

from lumi.sessions.message_visibility import should_show_human_message


@dataclass
class FakeMessage:
    """模拟 LangChain Message 对象。"""

    type: str = "human"
    content: str | list = ""
    additional_kwargs: dict = field(default_factory=dict)


def test_normal_message_visible():
    msg = FakeMessage(content="你好")
    assert should_show_human_message(msg) is True


def test_is_meta_hidden():
    msg = FakeMessage(
        content="<task-notification>...</task-notification>",
        additional_kwargs={"is_meta": True},
    )
    assert should_show_human_message(msg) is False


def test_is_meta_false_visible():
    msg = FakeMessage(content="普通消息", additional_kwargs={"is_meta": False})
    assert should_show_human_message(msg) is True


def test_empty_additional_kwargs_visible():
    msg = FakeMessage(content="普通消息", additional_kwargs={})
    assert should_show_human_message(msg) is True


def test_dict_message_with_is_meta():
    msg = {
        "type": "human",
        "content": "notification xml",
        "additional_kwargs": {"is_meta": True},
    }
    assert should_show_human_message(msg) is False


def test_dict_message_normal():
    msg = {"type": "human", "content": "你好"}
    assert should_show_human_message(msg) is True


def test_none_additional_kwargs():
    msg = FakeMessage(content="hello")
    msg.additional_kwargs = None  # type: ignore[assignment]
    assert should_show_human_message(msg) is True

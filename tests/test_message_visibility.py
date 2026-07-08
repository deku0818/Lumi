"""消息可见性判定测试：按 lumi.items 显示声明。"""

from __future__ import annotations

from dataclasses import dataclass, field

from lumi.sessions.message_visibility import should_show_human_message


@dataclass
class FakeMessage:
    """模拟 LangChain Message 对象。"""

    type: str = "human"
    content: str | list = ""
    additional_kwargs: dict = field(default_factory=dict)


def test_undeclared_message_visible():
    # 无声明（cron / 子 agent 等不经 bridge 的构造点）→ 显示，文本走 fallback
    msg = FakeMessage(content="你好")
    assert should_show_human_message(msg) is True


def test_declared_empty_hidden():
    # items: [] = 合成消息声明"无可显示"（后台通知 / 摘要 carrier / 工具回灌）
    msg = FakeMessage(
        content="<task-notification>...</task-notification>",
        additional_kwargs={"lumi": {"items": []}},
    )
    assert should_show_human_message(msg) is False


def test_declared_nonempty_visible():
    msg = FakeMessage(
        content="你好", additional_kwargs={"lumi": {"items": [{"text": "你好"}]}}
    )
    assert should_show_human_message(msg) is True


def test_meta_without_items_visible():
    # lumi 元数据存在但未声明 items（仅消息级 ts）→ 视同未声明，显示
    msg = FakeMessage(content="普通消息", additional_kwargs={"lumi": {"ts": 123}})
    assert should_show_human_message(msg) is True


def test_empty_additional_kwargs_visible():
    msg = FakeMessage(content="普通消息", additional_kwargs={})
    assert should_show_human_message(msg) is True


def test_dict_message_declared_empty_hidden():
    msg = {
        "type": "human",
        "content": "notification xml",
        "additional_kwargs": {"lumi": {"items": []}},
    }
    assert should_show_human_message(msg) is False


def test_dict_message_normal():
    msg = {"type": "human", "content": "你好"}
    assert should_show_human_message(msg) is True


def test_none_additional_kwargs():
    msg = FakeMessage(content="hello")
    msg.additional_kwargs = None  # type: ignore[assignment]
    assert should_show_human_message(msg) is True

"""bridge._build_user_message：显示声明（lumi.items）与附件注入的写侧规则。"""

from __future__ import annotations

from lumi.agents.core.meta_message import injected_prefix
from lumi.gateway.bridge.core import AgentBridge
from lumi.sessions.message_text import visible_user_text

_build = AgentBridge._build_user_message


def test_desktop_text_declares_single_item_with_ts_sunk():
    msg = _build("帮我看下部署", None, [])
    meta = msg.additional_kwargs["lumi"]
    # 消息级 ts 下沉到单条 item（规则在写侧，读侧 _user_items 纯投影）
    assert meta["items"] == [{"text": "帮我看下部署", "ts": meta["ts"]}]
    assert meta["ts"] > 0
    assert msg.content == "帮我看下部署"  # 无附件不动 content
    assert visible_user_text(msg) == "帮我看下部署"


def test_attachment_mounted_on_single_item_and_tag_injected():
    msg = _build("看下这个", None, ["/tmp/a.pdf"])
    meta = msg.additional_kwargs["lumi"]
    assert meta["items"] == [
        {
            "text": "看下这个",
            "files": [{"path": "/tmp/a.pdf", "name": "a.pdf"}],
            "ts": meta["ts"],
        }
    ]
    # 模型侧：标签块经 inject_text_into_message 前置并计数
    assert injected_prefix(msg) == 1
    assert msg.content[0]["text"] == "<attached-file>/tmp/a.pdf</attached-file>\n"
    # 显示侧：声明优先，标签不可见
    assert visible_user_text(msg) == "看下这个"


def test_attachment_only_message_has_no_empty_text_block():
    msg = _build("", None, ["/tmp/b.png"])
    meta = msg.additional_kwargs["lumi"]
    assert meta["items"] == [
        {"files": [{"path": "/tmp/b.png", "name": "b.png"}], "ts": meta["ts"]}
    ]
    assert visible_user_text(msg) == ""
    # 空串 content 不得残留空 text 块（Bedrock/严格端拒空白 text，消息永驻历史）
    assert msg.content == [
        {"type": "text", "text": "<attached-file>/tmp/b.png</attached-file>\n"}
    ]


def test_empty_text_no_attachment_declares_invisible():
    # 空文本无附件（仅裸 wire 客户端可达）：声明 items=[] 不可见，不产生空气泡
    msg = _build("", None, [])
    assert msg.additional_kwargs["lumi"]["items"] == []
    assert visible_user_text(msg) == ""


def test_feishu_items_preserved_and_multi_gets_separate_files_item():
    # IM 合并轮：items 自带（per-item ts 更精确不覆盖）；媒体归属未知 → 追加无名条目
    provided = {
        "items": [
            {"sender": "李雷", "ts": 1, "text": "看下"},
            {"sender": "韩梅梅", "ts": 2, "text": "加急"},
        ]
    }
    msg = _build("<sender>李雷</sender>\n看下…", provided, ["/tmp/c.txt"])
    items = msg.additional_kwargs["lumi"]["items"]
    assert items[0] == {"sender": "李雷", "ts": 1, "text": "看下"}
    assert items[1] == {"sender": "韩梅梅", "ts": 2, "text": "加急"}
    assert items[2] == {"files": [{"path": "/tmp/c.txt", "name": "c.txt"}]}

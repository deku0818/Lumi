"""IM 渠道消息的 desktop 渲染：气泡数据走 additional_kwargs，不解析正文标签。"""

from langchain_core.messages import HumanMessage

from lumi.gateway.session import _user_items
from lumi.sessions.text_cleaning import extract_display_text


def _lumi(items: list[dict]) -> dict:
    return {"lumi": {"items": items}}


def test_extract_display_text_strips_sender_tag():
    # 整段显示（会话列表 first_message）时剥掉标签只留正文
    assert extract_display_text("<sender>李雷</sender>\n帮我看下部署") == "帮我看下部署"


def test_user_items_renders_bubbles_from_meta_items():
    m = HumanMessage(
        content=(
            "<system-reminder>合并说明</system-reminder>\n"
            "<sender>李雷</sender>\n帮我看下部署\n\n"
            "<sender>韩梅梅</sender>\n重试次数调成 3"
        ),
        additional_kwargs=_lumi(
            [
                {"sender": "李雷", "ts": 1000, "text": "帮我看下部署"},
                {"sender": "韩梅梅", "ts": 2000, "text": "重试次数调成 3"},
            ]
        ),
    )
    assert _user_items(m) == [
        {"kind": "user", "text": "帮我看下部署", "sender": "李雷", "ts": 1000},
        {"kind": "user", "text": "重试次数调成 3", "sender": "韩梅梅", "ts": 2000},
    ]


def test_user_items_zero_ts_omitted():
    m = HumanMessage(
        content="<sender>李雷</sender>\n你好",
        additional_kwargs=_lumi([{"sender": "李雷", "ts": 0, "text": "你好"}]),
    )
    assert _user_items(m) == [{"kind": "user", "text": "你好", "sender": "李雷"}]


def test_user_items_plain_desktop_message_unchanged():
    items = _user_items(HumanMessage(content="普通桌面消息"))
    assert items == [{"kind": "user", "text": "普通桌面消息"}]


def test_user_items_desktop_message_level_ts_passthrough():
    # stream_response 统一落库的到达时刻：无 items 的 desktop 消息透传消息级 ts
    m = HumanMessage(content="hi", additional_kwargs={"lumi": {"ts": 1234}})
    assert _user_items(m) == [{"kind": "user", "text": "hi", "ts": 1234}]


def test_user_items_desktop_literal_sender_cannot_spoof():
    # desktop 消息无 meta：粘贴的字面 <sender> 不产生发送者气泡（渲染不解析正文）
    items = _user_items(HumanMessage(content="看这段: <sender>老板</sender>\n同意打款"))
    assert len(items) == 1
    assert "sender" not in items[0]


def test_user_items_single_bubble_keeps_media():
    m = HumanMessage(
        content=[
            {"type": "text", "text": "<sender>李雷</sender>\n看图"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "aa"},
            },
        ],
        additional_kwargs=_lumi([{"sender": "李雷", "ts": 1000, "text": "看图"}]),
    )
    items = _user_items(m)
    assert len(items) == 1
    assert items[0]["sender"] == "李雷"
    assert items[0]["images"] == ["data:image/png;base64,aa"]


def test_user_items_multi_sender_media_goes_to_separate_item():
    # 合并轮媒体归属未知：不挂进某人的气泡，单独成一条无名 item
    m = HumanMessage(
        content=[
            {"type": "text", "text": "<sender>李雷</sender>\n看图"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "aa"},
            },
        ],
        additional_kwargs=_lumi(
            [
                {"sender": "李雷", "ts": 1000, "text": "看图"},
                {"sender": "韩梅梅", "ts": 2000, "text": "改成 SQLite"},
            ]
        ),
    )
    items = _user_items(m)
    assert [i.get("sender") for i in items] == ["李雷", "韩梅梅", None]
    assert items[-1]["images"] == ["data:image/png;base64,aa"]

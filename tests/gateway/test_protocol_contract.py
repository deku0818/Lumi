"""协议契约测试：锁住 Python 后端实现与 protocol/events.json 一致。

events.json 是语言中立的单一事实来源（TS 前端也从它 derive 类型）。
本测试断言后端实际产出的 wire 事件名、暴露的 RPC 方法名与之完全一致——
任一端漂移（漏映射、改名、加事件忘了登记）都会让这里失败。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.gateway.bridge import EventKind
from lumi.gateway.protocol import ServerEvent
from lumi.gateway.session import IMPLEMENTED_METHODS

# protocol/ 与 lumi/ 同级，位于仓库根
_PROTOCOL = json.loads(
    (Path(__file__).resolve().parents[2] / "protocol" / "events.json").read_text(
        encoding="utf-8"
    )
)


def test_event_names_match_source_of_truth():
    """后端产出的全部 wire 事件名（EventKind + ServerEvent 成员值）== events.json 声明。"""
    produced = {str(e) for e in EventKind} | {str(e) for e in ServerEvent}
    declared = set(_PROTOCOL["events"])
    assert produced == declared, (
        f"协议漂移：后端独有={produced - declared}，json 独有={declared - produced}"
    )


def test_rpc_methods_match_source_of_truth():
    """ws.py 实现的 RPC 方法 == events.json 声明的集合。"""
    declared = set(_PROTOCOL["methods"])
    assert set(IMPLEMENTED_METHODS) == declared, (
        f"协议漂移：实现独有={set(IMPLEMENTED_METHODS) - declared}，"
        f"json 独有={declared - set(IMPLEMENTED_METHODS)}"
    )


def test_event_payload_keys_match_source_of_truth():
    """每个 EventKind 的 payload 字段 ⊆ events.json 声明，且必填字段齐全。

    只比名字的话，字段漂移（后端改了键名 / 前端类型多了后端从不发的键）照样绿。
    CLARIFY / APPROVAL 是 data 原样透传，形状由各自的构造点决定，这里不管。
    """
    from lumi.gateway.bridge import BridgeEvent
    from lumi.gateway.protocol import _payload

    for kind in EventKind:
        if kind in (EventKind.CLARIFY, EventKind.APPROVAL):
            continue
        evt = BridgeEvent(
            kind=kind,
            text="t",
            message_id="m",
            name="n",
            args={"a": 1},
            tool_call_id="c",
            output="o",
            data={"active": True},
            error="e",
            is_error=True,
            usage_metadata={"input_tokens": 1},
            run_id="r",
        )
        declared = _PROTOCOL["events"][str(kind)]["payload"]
        allowed = {k.rstrip("?") for k in declared}
        required = {k for k in declared if not k.endswith("?")}
        keys = set(_payload(evt))
        assert keys <= allowed, f"{kind}: 未声明的字段 {keys - allowed}"
        assert required <= keys, f"{kind}: 缺必填字段 {required - keys}"

"""协议契约测试：锁住 Python 后端实现与 protocol/events.json 一致。

events.json 是语言中立的单一事实来源（TS 前端也从它 derive 类型）。
本测试断言后端实际产出的 wire 事件名、暴露的 RPC 方法名与之完全一致——
任一端漂移（漏映射、改名、加事件忘了登记）都会让这里失败。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.gateway.bridge import EventKind
from lumi.gateway.session import IMPLEMENTED_METHODS

# protocol/ 与 lumi/ 同级，位于仓库根
_PROTOCOL = json.loads(
    (Path(__file__).resolve().parents[2] / "protocol" / "events.json").read_text(
        encoding="utf-8"
    )
)

# 服务端直接发出但不经 EventKind 的事件（握手帧 + cron/bg/渠道广播）
_DIRECT_EVENTS = {
    "gateway.ready",
    "cron.result",
    "cron.running",
    "bg_tasks.update",
    "channel.activity",
    "session.title",
}


def test_event_names_match_source_of_truth():
    """后端产出的全部 wire 事件名（EventKind 值 + 握手帧）== events.json 声明。"""
    produced = {str(e) for e in EventKind} | _DIRECT_EVENTS
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

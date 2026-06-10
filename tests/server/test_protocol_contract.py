"""协议契约测试：锁住 Python 后端实现与 protocol/events.json 一致。

events.json 是语言中立的单一事实来源（TS 前端也从它 derive 类型）。
本测试断言后端实际产出的 wire 事件名、暴露的 RPC 方法名与之完全一致——
任一端漂移（漏映射、改名、加事件忘了登记）都会让这里失败。
"""

from __future__ import annotations

import json
from pathlib import Path

from lumi.agents.bridge import EventKind

# protocol/ 与 lumi/ 同级，位于仓库根
_PROTOCOL = json.loads(
    (Path(__file__).resolve().parents[2] / "protocol" / "events.json").read_text(
        encoding="utf-8"
    )
)

# ws.py 直接发出但不经 EventKind 的事件（握手帧）
_DIRECT_EVENTS = {"gateway.ready"}

# ws.py 实现的 RPC 方法（_dispatch 非流式 + endpoint 流式 task，与 lumi/server/ws.py 同步）
_IMPLEMENTED_METHODS = {
    "send_message",
    "resume",
    "stop",
    "list_commands",
    "run_command",
    "list_providers",
    "test_provider",
    "set_provider",
    "save_provider",
    "delete_provider",
    "list_sessions",
    "new_session",
    "switch_session",
    "load_history",
    "pin_session",
    "rename_session",
    "delete_session",
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
    assert _IMPLEMENTED_METHODS == declared, (
        f"协议漂移：实现独有={_IMPLEMENTED_METHODS - declared}，"
        f"json 独有={declared - _IMPLEMENTED_METHODS}"
    )

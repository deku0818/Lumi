"""message.complete 的用量只认主链 CallModel 与子代理（纯函数断言，不跑图）。

AutoClassify / 工具内 / goal 判官等内部模型调用若带 usage 下发，会覆盖前端上下文环，
读数在主调用（~156k）与分类器（~1.6k）之间来回跳。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from lumi.gateway.bridge import AgentBridge
from lumi.gateway.bridge.core import EventKind

USAGE = {"input_tokens": 1609, "output_tokens": 156, "total_tokens": 1765}


def _complete(bridge: AgentBridge, node: str, parent_ids: list[str] | None = None):
    event = {
        "event": "on_chat_model_end",
        "run_id": "r",
        "parent_ids": parent_ids or [],
        "metadata": {"langgraph_node": node},
        "data": {"output": AIMessage(content="x", usage_metadata=USAGE)},
    }
    (evt,) = list(bridge._translate_event(event))
    assert evt.kind == EventKind.MESSAGE_COMPLETE
    return evt


def test_main_call_model_carries_usage():
    assert _complete(AgentBridge(), "CallModel").usage_metadata


def test_internal_node_drops_usage():
    assert not _complete(AgentBridge(), "AutoClassify").usage_metadata
    assert not _complete(AgentBridge(), "ToolExecutor").usage_metadata


def test_subagent_keeps_usage():
    bridge = AgentBridge()
    bridge._active_agent_runs.add("sub")
    evt = _complete(bridge, "AutoClassify", ["root", "sub"])
    assert evt.parent_run_id == "sub" and evt.usage_metadata

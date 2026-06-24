"""多层委派下子代理事件归属测试（纯函数断言，不跑图）。

parent_ids 为 root→直接父顺序。深层（孙及更深）活动应统一归并到主 agent
直接派生的「顶层子代理」，避免随机错挂/丢弃。
"""

from __future__ import annotations

from lumi.gateway.bridge import AgentBridge

# 模拟一条三层委派的祖先链：root → main → 顶层子代理 → 孙代理
ROOT = "root"
MAIN = "main"
SUB = "sub-top"  # 主 agent 直接派生的顶层子代理（agent 工具 run）
GRAND = "sub-grand"  # 子代理再派生的孙代理（agent 工具 run）


def _bridge(*active: str) -> AgentBridge:
    bridge = AgentBridge()
    for run_id in active:
        bridge._active_agent_runs[run_id] = None
    return bridge


def test_no_active_agent_returns_empty():
    bridge = _bridge()
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN]) == ""


def test_single_level_attributes_to_sub():
    bridge = _bridge(SUB)
    parent = bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB])
    assert parent == SUB


def test_grandchild_rolls_up_to_top_sub():
    """孙代理事件归并到顶层子代理（最浅祖先），而非孙自己或直接父。"""
    bridge = _bridge(SUB, GRAND)
    parent = bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB, GRAND])
    assert parent == SUB


def test_excludes_self_run_id():
    """agent 工具自身的 on_tool_start 不应把自己判为子代理事件。"""
    bridge = _bridge(SUB)
    # 事件自身 run_id == SUB（agent 工具刚启动那一刻），祖先里只有 main
    assert bridge._resolve_subagent_parent(SUB, [ROOT, MAIN]) == ""


def test_deterministic_regardless_of_insertion_order():
    """活跃集合插入序与归属无关——归属只由 parent_ids 顺序决定。"""
    a = _bridge(GRAND, SUB)  # 故意先插 GRAND
    b = _bridge(SUB, GRAND)
    chain = [ROOT, MAIN, SUB, GRAND]
    assert a._resolve_subagent_parent("evt", chain) == SUB
    assert b._resolve_subagent_parent("evt", chain) == SUB


def test_subagent_marker_returns_top_sub_by_insertion_order():
    """中断标记无 parent_ids，取最早插入且仍活跃的 run = 顶层子代理。"""
    bridge = _bridge(SUB, GRAND)
    assert bridge._subagent_marker() == SUB
    # 顶层子代理结束后，标记落到仍活跃的孙代理
    bridge._active_agent_runs.pop(SUB, None)
    assert bridge._subagent_marker() == GRAND
    bridge._active_agent_runs.pop(GRAND, None)
    assert bridge._subagent_marker() == ""

"""多层委派下子代理事件归属测试（纯函数断言，不跑图）。

parent_ids 为 root→直接父顺序。流式事件与在途审批卡片均走同一归属
（_resolve_subagent_parent）：祖先链中「最浅」的活跃 agent run 即主 agent 直接派生的
顶层子代理，深层活动据此归并。在途审批后审批卡片自带 parent_ids，并行兄弟也能各自
正确归属（旧 _subagent_marker 无 parent_ids 时只能放弃挂主 agent，已退场）。
"""

from __future__ import annotations

from lumi.gateway.bridge import AgentBridge

ROOT = "root"
MAIN = "main"
SUB = "sub-top"  # 主 agent 直接派生的顶层子代理（agent 工具 run）
GRAND = "sub-grand"  # 子代理再派生的孙代理（agent 工具 run）
SUB2 = "sub-top-2"  # 与 SUB 同级的并行兄弟顶层子代理


def _bridge(active: list[str] | None = None) -> AgentBridge:
    """active: 活跃 agent run_id 集合（祖先链由各事件的 parent_ids 携带）。"""
    bridge = AgentBridge()
    bridge._active_agent_runs.update(active or [])
    return bridge


def test_no_active_agent_returns_empty():
    bridge = _bridge()
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN]) == ""


def test_single_level_attributes_to_sub():
    bridge = _bridge([SUB])
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB]) == SUB


def test_grandchild_rolls_up_to_top_sub():
    """孙代理事件归并到顶层子代理（最浅祖先），而非孙自己或直接父。"""
    bridge = _bridge([SUB, GRAND])
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB, GRAND]) == SUB


def test_excludes_self_run_id():
    """agent 工具自身的 on_tool_start 不应把自己判为子代理事件。"""
    bridge = _bridge([SUB])
    assert bridge._resolve_subagent_parent(SUB, [ROOT, MAIN]) == ""


def test_deterministic_regardless_of_insertion_order():
    """归属只由 parent_ids 顺序决定，与活跃集插入序无关。"""
    chain = [ROOT, MAIN, SUB, GRAND]
    a = _bridge([GRAND, SUB])  # 故意先插 GRAND
    b = _bridge([SUB, GRAND])
    assert a._resolve_subagent_parent("evt", chain) == SUB
    assert b._resolve_subagent_parent("evt", chain) == SUB


def test_agent_run_stays_active_after_tool_end():
    """后台子代理：agent 工具立即返回（on_tool_end）后其事件仍须归属到卡片。

    回归锁：若 on_tool_end 移除 run_id，后台子代理的后续事件会以 parent_id=""
    泄漏进主流（截断主回复气泡、散落工具卡）。
    """
    bridge = _bridge()
    bridge._track_agent_run("on_tool_start", "agent", SUB)
    bridge._track_agent_run("on_tool_end", "agent", SUB)
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB]) == SUB


def test_track_ignores_non_agent_tools():
    bridge = _bridge()
    bridge._track_agent_run("on_tool_start", "bash", "run-x")
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, "run-x"]) == ""


def test_parallel_siblings_each_attribute_by_own_parent_ids():
    """并行兄弟同时活跃：审批/事件自带各自 parent_ids，能精确归属到各自顶层子代理。

    这是在途审批相较旧 _subagent_marker（无 parent_ids 时放弃、挂主 agent）的改进。
    """
    bridge = _bridge([SUB, SUB2])
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB]) == SUB
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB2]) == SUB2

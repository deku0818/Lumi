"""多层委派下子代理事件归属测试（纯函数断言，不跑图）。

parent_ids 为 root→直接父顺序。
- 流式路径（_resolve_subagent_parent）：深层活动归并到主 agent 直接派生的顶层子代理。
- 中断路径（_subagent_marker，无 parent_ids）：靠活跃集存下的 parent_ids 判断祖先关系，
  单链委派归属到唯一顶层；并行兄弟无法区分时返回空串（挂主 agent，不自信错挂）。
"""

from __future__ import annotations

from lumi.gateway.bridge import AgentBridge

ROOT = "root"
MAIN = "main"
SUB = "sub-top"  # 主 agent 直接派生的顶层子代理（agent 工具 run）
GRAND = "sub-grand"  # 子代理再派生的孙代理（agent 工具 run）
SUB2 = "sub-top-2"  # 与 SUB 同级的并行兄弟顶层子代理


def _bridge(active: dict[str, list[str]] | None = None) -> AgentBridge:
    """active: {活跃 agent run_id: 其 parent_ids}。"""
    bridge = AgentBridge()
    for run_id, parent_ids in (active or {}).items():
        bridge._active_agent_runs[run_id] = parent_ids
    return bridge


# --- 流式路径：_resolve_subagent_parent（只用 key 成员判定 + parent_ids 顺序）---


def test_no_active_agent_returns_empty():
    bridge = _bridge()
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN]) == ""


def test_single_level_attributes_to_sub():
    bridge = _bridge({SUB: [ROOT, MAIN]})
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB]) == SUB


def test_grandchild_rolls_up_to_top_sub():
    """孙代理事件归并到顶层子代理（最浅祖先），而非孙自己或直接父。"""
    bridge = _bridge({SUB: [ROOT, MAIN], GRAND: [ROOT, MAIN, SUB]})
    assert bridge._resolve_subagent_parent("evt", [ROOT, MAIN, SUB, GRAND]) == SUB


def test_excludes_self_run_id():
    """agent 工具自身的 on_tool_start 不应把自己判为子代理事件。"""
    bridge = _bridge({SUB: [ROOT, MAIN]})
    assert bridge._resolve_subagent_parent(SUB, [ROOT, MAIN]) == ""


def test_deterministic_regardless_of_insertion_order():
    """归属只由 parent_ids 顺序决定，与活跃集插入序无关。"""
    chain = [ROOT, MAIN, SUB, GRAND]
    a = _bridge({GRAND: [ROOT, MAIN, SUB], SUB: [ROOT, MAIN]})  # 故意先插 GRAND
    b = _bridge({SUB: [ROOT, MAIN], GRAND: [ROOT, MAIN, SUB]})
    assert a._resolve_subagent_parent("evt", chain) == SUB
    assert b._resolve_subagent_parent("evt", chain) == SUB


# --- 中断路径：_subagent_marker（无 parent_ids，靠祖先关系定唯一顶层）---


def test_marker_single_chain_attributes_to_top_sub():
    """单链委派（SUB→GRAND 都活跃）：唯一顶层是 SUB，归属到 SUB。"""
    bridge = _bridge({SUB: [ROOT, MAIN], GRAND: [ROOT, MAIN, SUB]})
    assert bridge._subagent_marker() == SUB
    # 顶层 SUB 结束后，仅剩 GRAND（其父 SUB 已不活跃）→ GRAND 成为唯一顶层
    bridge._active_agent_runs.pop(SUB, None)
    assert bridge._subagent_marker() == GRAND
    bridge._active_agent_runs.pop(GRAND, None)
    assert bridge._subagent_marker() == ""


def test_marker_parallel_siblings_returns_empty():
    """并行兄弟（SUB / SUB2 同级）同时活跃且触发中断：无法区分 → 返回空串挂主 agent。"""
    bridge = _bridge({SUB: [ROOT, MAIN], SUB2: [ROOT, MAIN]})
    assert bridge._subagent_marker() == ""


def test_marker_single_active_attributes_to_it():
    """只有一个活跃子代理：明确归属。"""
    bridge = _bridge({SUB: [ROOT, MAIN]})
    assert bridge._subagent_marker() == SUB


def test_marker_no_active_returns_empty():
    assert _bridge()._subagent_marker() == ""

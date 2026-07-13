"""目标驱动 hook：判官三态路由 + 转录截断 + 注册顺序（与 dream 共存）。

判官（LLM 调用）在此全部 mock——按项目规范不跑真实模型，只验 hook 的路由逻辑。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lumi.agents.core.hooks import AdditionalContext, HookContext, iter_hooks
from lumi.agents.core.hooks import goal as goal_hook
from lumi.sessions import session_meta

THREAD = "t-goal"


@pytest.fixture
def meta_file(tmp_path, monkeypatch):
    monkeypatch.setattr(session_meta, "_meta_path", lambda: tmp_path / "meta.json")
    return tmp_path / "meta.json"


def _ctx(messages=None):
    return HookContext(
        state={"messages": messages or []},
        config={"configurable": {"thread_id": THREAD}},
        event="Stop",
        payload={},
    )


def _mock_judge(monkeypatch, *, ok, impossible=False, reason="r"):
    async def fake(condition, messages):
        return goal_hook._GoalVerdict(ok=ok, impossible=impossible, reason=reason)

    monkeypatch.setattr(goal_hook, "_judge", fake)


# === 路由：无 goal 直接放行 ===


async def test_no_goal_passes_through(meta_file):
    assert await goal_hook.goal_stop_hook(_ctx()) is None


async def test_no_thread_id_passes_through(meta_file):
    ctx = HookContext(state={"messages": []}, config={}, event="Stop", payload={})
    assert await goal_hook.goal_stop_hook(ctx) is None


async def test_sub_agent_depth_passes_through(meta_file, monkeypatch):
    # 子 agent（depth>0）继承父 thread_id，但不该参与目标驱动——即便父有活跃 goal
    session_meta.update_meta(THREAD, goal="建 hello.txt")
    _mock_judge(monkeypatch, ok=False, reason="不该被调用")
    ctx = HookContext(
        state={"messages": [], "depth": 1},
        config={"configurable": {"thread_id": THREAD}},
        event="Stop",
        payload={},
    )
    assert await goal_hook.goal_stop_hook(ctx) is None
    assert session_meta.get_goal(THREAD) == "建 hello.txt"  # 未被误清


# === 路由：三态 ===


async def test_ok_true_clears_goal_and_passes(meta_file, monkeypatch):
    session_meta.update_meta(THREAD, goal="建 hello.txt")
    _mock_judge(monkeypatch, ok=True, reason="已建好")

    assert await goal_hook.goal_stop_hook(_ctx()) is None  # 放行 → dream 可触发
    assert session_meta.get_goal(THREAD) == ""  # 达成自动解除


async def test_ok_false_pulls_back_with_reason(meta_file, monkeypatch):
    session_meta.update_meta(THREAD, goal="建 hello.txt")
    _mock_judge(monkeypatch, ok=False, reason="文件还没建")

    result = await goal_hook.goal_stop_hook(_ctx())
    assert isinstance(result, AdditionalContext)  # 短路 → dream 不触发
    assert "文件还没建" in result.text
    assert session_meta.get_goal(THREAD) == "建 hello.txt"  # 未达成 → 条件保留


async def test_impossible_clears_goal_and_passes(meta_file, monkeypatch):
    session_meta.update_meta(THREAD, goal="连上一个不存在的服务")
    _mock_judge(monkeypatch, ok=False, impossible=True, reason="服务不可用")

    assert await goal_hook.goal_stop_hook(_ctx()) is None  # 放行结束
    assert session_meta.get_goal(THREAD) == ""  # 永远达不成 → 解除


async def test_clear_preserves_other_marks(meta_file, monkeypatch):
    # ok:true 清 goal 用空值 update，不该动 pin
    session_meta.update_meta(THREAD, pinned=True, goal="建 hello.txt")
    _mock_judge(monkeypatch, ok=True, reason="done")

    await goal_hook.goal_stop_hook(_ctx())
    assert session_meta.load_all()[THREAD]["pinned"] is True


# === 转录截断 ===


def test_render_transcript_no_truncation():
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    text = goal_hook._render_transcript(msgs)
    assert "较早的对话已被截断" not in text
    assert "hello" in text


def test_render_transcript_truncates_head_and_counts(monkeypatch):
    # 把 context_length 调到极小逼出截断；预算 = 20 * 0.8 * 3 = 48 字节
    fake_cfg = type(
        "C",
        (),
        {"config": type("D", (), {"token": type("T", (), {"context_length": 20})()})()},
    )()
    monkeypatch.setattr(goal_hook, "get_config", lambda: fake_cfg)

    msgs = [AIMessage(content=f"message-number-{i:03d}-padding") for i in range(10)]
    text = goal_hook._render_transcript(msgs)

    assert "较早的对话已被截断" in text
    assert "省略了前面" in text
    # 保尾丢头：最后一条在，最早一条被省略
    assert "message-number-009" in text
    assert "message-number-000" not in text


def test_render_transcript_counts_tool_call_bytes(monkeypatch):
    # #5 回归：tool_calls 的 AI 消息 content 为空，但渲染出大 name({args})——预算须按
    # 实际渲染字节算，否则工具密集尾部被低估、突破窗口。
    fake_cfg = type(
        "C",
        (),
        {
            "config": type(
                "D", (), {"token": type("T", (), {"context_length": 100})()}
            )()
        },
    )()
    monkeypatch.setattr(
        goal_hook, "get_config", lambda: fake_cfg
    )  # budget = 100*0.8*3 = 240B

    big = AIMessage(
        content="",
        tool_calls=[
            {"name": "write", "id": "1", "args": {"path": "x", "content": "A" * 300}}
        ],
    )
    small = AIMessage(content="尾巴")
    text = goal_hook._render_transcript([big, small])
    # big 单条已超预算 → 被丢，触发截断说明；若只算 content(=0) 则不会截断
    assert "较早的对话已被截断" in text
    assert "尾巴" in text


def test_render_transcript_omitted_excludes_system(monkeypatch):
    # #6 回归：被丢的 system 消息不渲染，不该计入 N
    from langchain_core.messages import SystemMessage

    fake_cfg = type(
        "C",
        (),
        {"config": type("D", (), {"token": type("T", (), {"context_length": 20})()})()},
    )()
    monkeypatch.setattr(goal_hook, "get_config", lambda: fake_cfg)  # budget = 48B

    msgs = [
        SystemMessage(content="系统提示"),
        AIMessage(content="老消息-padding-xxxx"),
        AIMessage(content="新消息-padding-yyyy"),
    ]
    text = goal_hook._render_transcript(msgs)
    # system + 1 条老 AI 被丢，但 N 只数会渲染的 → 1（不是 2）
    assert "省略了前面 1 条消息" in text


# === 集成：走真实 dispatch 链（builtin 全注册）===


async def test_dispatch_chain_pulls_back_via_command(meta_file, monkeypatch):
    """真实 Stop dispatch：structured 放行 → goal 拦截 → 翻译成 Command 拉回 CallModel。

    覆盖 goal_stop_hook 单测之外的接缝：dispatch first_intercept 顺序 + AdditionalContext
    → Command(goto=CallModel) 翻译 + reminder 消息注入。"""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    import lumi.agents.core.hooks.builtin  # noqa: F401  触发注册
    from lumi.agents.core.hooks import dispatch_hooks

    session_meta.update_meta(THREAD, goal="建 hello.txt")
    _mock_judge(monkeypatch, ok=False, reason="文件还没建")

    cmd = await dispatch_hooks("Stop", _ctx(), default_goto="CallModel")
    assert isinstance(cmd, Command)
    assert cmd.goto == "CallModel"  # 未达成 → 拉回继续
    msgs = cmd.update["messages"]
    assert any(
        isinstance(m, HumanMessage) and "文件还没建" in str(m.content) for m in msgs
    )


async def test_dispatch_chain_passes_when_no_goal(meta_file):
    """无 goal：dispatch 链无人拦截（dream 门控默认关也返 None）→ 返回 None 放行 END。"""
    import lumi.agents.core.hooks.builtin  # noqa: F401
    from lumi.agents.core.hooks import dispatch_hooks

    cmd = await dispatch_hooks("Stop", _ctx(), default_goto="CallModel")
    assert cmd is None


# === 注册顺序：与 dream 共存 ===


def test_stop_hook_order_goal_between_structured_and_dream():
    import lumi.agents.core.hooks.builtin  # noqa: F401  触发注册

    names = [h.__name__ for h in iter_hooks("Stop")]
    assert (
        names.index("structured_output_stop_hook")
        < names.index("goal_stop_hook")
        < names.index("auto_dream_stop_hook")
    ), names

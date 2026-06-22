"""Hook 框架内核测试：dispatch 三模式 + 返回值翻译 + 错误隔离 + 注册 API。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command

from lumi.agents.core.hooks import (
    AdditionalContext,
    Block,
    HookContext,
    dispatch_hooks,
    iter_hooks,
    register_hook,
    replace_hooks,
    set_run_config_hooks,
    unregister_hook,
)
from lumi.agents.core.hooks import dispatch as dispatch_mod


@pytest.fixture(autouse=True)
def clean_hooks():
    """每个用例前后清空全局 _HOOKS，避免跨用例污染。"""
    saved = {k: list(v) for k, v in dispatch_mod._HOOKS.items()}
    dispatch_mod._HOOKS.clear()
    yield
    dispatch_mod._HOOKS.clear()
    dispatch_mod._HOOKS.update(saved)


def _ctx(event="Stop", state=None):
    return HookContext(state=state or {}, config={}, event=event, payload={})


def _hook(result, *, record=None, name="h"):
    """构造一个返回固定结果的 async hook，可选记录调用顺序。"""

    async def hook(ctx):
        if record is not None:
            record.append(name)
        return result

    return hook


# === dispatch: 基础 ===


async def test_dispatch_no_hooks_returns_none():
    assert await dispatch_hooks("Stop", _ctx()) is None


# === dispatch: first_intercept ===


async def test_first_intercept_stops_at_first_non_none():
    record: list[str] = []
    register_hook("Stop", _hook(None, record=record, name="a"))
    register_hook("Stop", _hook(AdditionalContext("拉回"), record=record, name="b"))
    register_hook("Stop", _hook(Block("不该跑"), record=record, name="c"))

    cmd = await dispatch_hooks("Stop", _ctx(), mode="first_intercept")

    assert isinstance(cmd, Command)
    assert cmd.goto == "CallModel"
    assert record == ["a", "b"]  # c 未执行


async def test_first_intercept_all_none_returns_none():
    register_hook("Stop", _hook(None))
    register_hook("Stop", _hook(None))
    assert await dispatch_hooks("Stop", _ctx(), mode="first_intercept") is None


# === dispatch: collect ===


async def test_collect_merges_additional_contexts():
    register_hook("PreToolUse", _hook(AdditionalContext("提醒一")))
    register_hook("PreToolUse", _hook(None))
    register_hook("PreToolUse", _hook(AdditionalContext("提醒二")))

    cmd = await dispatch_hooks(
        "PreToolUse", _ctx("PreToolUse"), mode="collect", default_goto="ToolExecutor"
    )

    assert isinstance(cmd, Command)
    assert cmd.goto == "ToolExecutor"
    msgs = cmd.update["messages"]
    assert len(msgs) == 2
    assert all(isinstance(m, HumanMessage) for m in msgs)
    assert "提醒一" in msgs[0].content[0]["text"]
    assert "提醒二" in msgs[1].content[0]["text"]


async def test_collect_block_short_circuits_but_keeps_collected():
    record: list[str] = []
    register_hook(
        "PreToolUse", _hook(AdditionalContext("先收"), record=record, name="a")
    )
    register_hook("PreToolUse", _hook(Block("拦截"), record=record, name="b"))
    register_hook(
        "PreToolUse", _hook(AdditionalContext("不该收"), record=record, name="c")
    )

    cmd = await dispatch_hooks("PreToolUse", _ctx("PreToolUse"), mode="collect")

    assert isinstance(cmd, Command)
    assert cmd.goto == END
    assert record == ["a", "b"]  # c 未执行
    msgs = cmd.update["messages"]
    # 已收的 reminder + Block 的 AIMessage
    assert isinstance(msgs[0], HumanMessage)
    assert "先收" in msgs[0].content[0]["text"]
    assert isinstance(msgs[-1], AIMessage)
    assert msgs[-1].content == "拦截"


async def test_collect_all_none_returns_none():
    register_hook("PreToolUse", _hook(None))
    register_hook("PreToolUse", _hook(None))
    assert (
        await dispatch_hooks("PreToolUse", _ctx("PreToolUse"), mode="collect") is None
    )


# === dispatch: side_effect ===


async def test_side_effect_runs_all_ignores_returns():
    record: list[str] = []
    register_hook("SessionEnd", _hook(Block("被忽略"), record=record, name="a"))
    register_hook(
        "SessionEnd", _hook(AdditionalContext("也忽略"), record=record, name="b")
    )

    cmd = await dispatch_hooks("SessionEnd", _ctx("SessionEnd"), mode="side_effect")

    assert cmd is None
    assert sorted(record) == ["a", "b"]  # 都跑了（并发，顺序不定）


# === 返回值翻译 ===


async def test_passthrough_command_returned_as_is():
    sentinel = Command(goto="Custom", update={"foo": 1})
    register_hook("Stop", _hook(sentinel))
    cmd = await dispatch_hooks("Stop", _ctx())
    assert cmd is sentinel


async def test_additional_context_wraps_system_reminder():
    register_hook("Stop", _hook(AdditionalContext("继续干")))
    cmd = await dispatch_hooks("Stop", _ctx(), default_goto="CallModel")
    msg = cmd.update["messages"][0]
    text = msg.content[0]["text"]
    assert text.startswith("<system-reminder>")
    assert "继续干" in text
    assert text.rstrip().endswith("</system-reminder>")


async def test_additional_context_is_marked_meta_and_reminder():
    # 注入的 reminder 既是 is_meta（TUI 不渲染为用户气泡）又是 is_hook_reminder
    # （轮边界扫描精确跳过它，区别于后台通知等真实 meta）。
    from lumi.agents.core.meta_message import is_meta_message, is_reminder_message

    register_hook("Stop", _hook(AdditionalContext("继续干")))
    cmd = await dispatch_hooks("Stop", _ctx())
    msg = cmd.update["messages"][0]
    assert is_meta_message(msg)
    assert is_reminder_message(msg)


async def test_block_routes_to_end():
    register_hook("Stop", _hook(Block("到此为止")))
    cmd = await dispatch_hooks("Stop", _ctx())
    assert cmd.goto == END
    assert cmd.update["messages"][0].content == "到此为止"


# === 错误隔离 ===


async def test_hook_exception_isolated():
    record: list[str] = []

    async def boom(ctx):
        record.append("boom")
        raise RuntimeError("故意炸")

    register_hook("Stop", boom)
    register_hook("Stop", _hook(AdditionalContext("我还在"), record=record, name="ok"))

    cmd = await dispatch_hooks("Stop", _ctx())

    assert record == ["boom", "ok"]  # 抛错后继续下一个
    assert "我还在" in cmd.update["messages"][0].content[0]["text"]


# === 注册 API ===


async def test_run_config_hooks_run_before_builtin_and_isolated():
    """per-run config hook（项目级）先于进程全局 builtin 执行；清除后只剩 builtin。

    项目随会话绑定：config hook 经 contextvar 注入，按会话隔离、并发互不串。
    """
    record: list[str] = []
    register_hook("Stop", _hook(None, record=record, name="builtin"))
    set_run_config_hooks({"Stop": [_hook(None, record=record, name="config")]})
    await dispatch_hooks("Stop", _ctx())
    assert record == ["config", "builtin"]  # config 先于 builtin

    record.clear()
    set_run_config_hooks(None)  # 清除本 run 的 config hook
    await dispatch_hooks("Stop", _ctx())
    assert record == ["builtin"]  # 只剩进程全局 builtin


def test_unregister_removes_hook():
    hook = _hook(None)
    register_hook("Stop", hook)
    assert iter_hooks("Stop") == [hook]
    assert unregister_hook("Stop", hook) is True
    assert iter_hooks("Stop") == []
    assert unregister_hook("Stop", hook) is False


async def test_replace_hooks_restores_after_exit():
    original = _hook(None)
    register_hook("Stop", original)
    with replace_hooks("Stop", [_hook(Block("临时"))]):
        cmd = await dispatch_hooks("Stop", _ctx())
        assert cmd.goto == END
    assert iter_hooks("Stop") == [original]

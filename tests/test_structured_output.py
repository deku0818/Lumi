"""结构化输出增强测试：JSON Schema 校验 + 连续失败保护 + 真工具闭包 +
Stop hook 联动 + enrich。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from lumi.agents.core import nodes
from lumi.agents.core.hooks import AdditionalContext, HookContext, replace_hooks
from lumi.agents.core.hooks.builtin import (
    MAX_STOP_PULLBACKS,
    structured_output_stop_hook,
)
from lumi.agents.core.nodes import (
    _structured_output_abort_message,
    is_use_tool,
    tool_executor,
)
from lumi.agents.core.state import LumiAgentContext
from lumi.agents.core.structured_tool import (
    MAX_CONSECUTIVE_FAILURES,
    STRUCTURED_OUTPUT_REMINDER,
    count_consecutive_structured_output_failures,
    create_structured_output_tool,
    validate_structured_output,
)

TOOL = "__structured_output__"
SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name"],
}


def _runtime(tools):
    return SimpleNamespace(context=LumiAgentContext(tools=tools))


class _StructuredFakeToolNode:
    """绕过真实 ToolNode 的 graph-runtime 注入，但调用**真实** structured 闭包。

    保留真实校验逻辑（闭包内 jsonschema），只替换 ToolNode 这层基础设施——
    真实 ToolNode 在 graph 外执行需 LangGraph runtime context。
    """

    def __init__(self, tools, handle_tool_errors=None):
        self._tools = {t.name: t for t in tools}

    async def ainvoke(self, state):
        tc = state["messages"][-1].tool_calls[0]
        tool = self._tools[tc["name"]]
        result = await tool.ainvoke({**tc, "type": "tool_call"})
        if isinstance(result, Command):
            return result
        return {"messages": [result]}


class _MixedFakeToolNode:
    """模拟一轮里 structured_output(Command) + 普通工具(ToolMessage) 混合返回。"""

    def __init__(self, tools, handle_tool_errors=None):
        pass

    async def ainvoke(self, state):
        return [
            Command(
                update={
                    "structured_output": {"name": "Lumi"},
                    "messages": [_ok_tm("1")],
                }
            ),
            ToolMessage(content="x" * 100, tool_call_id="2", name="bash"),
        ]


def _err_tm(tc_id):
    return ToolMessage(content="x", tool_call_id=tc_id, name=TOOL, status="error")


def _ok_tm(tc_id):
    return ToolMessage(content="ok", tool_call_id=tc_id, name=TOOL)


def _call(args, tc_id="1"):
    return AIMessage(content="", tool_calls=[{"name": TOOL, "args": args, "id": tc_id}])


# === JSON Schema 校验 ===


def test_validate_pass():
    assert validate_structured_output({"name": "a", "age": 3}, SCHEMA) == []


def test_validate_missing_required():
    errs = validate_structured_output({"age": 3}, SCHEMA)
    assert errs and any("name" in e for e in errs)


def test_validate_empty_schema_no_constraint():
    assert validate_structured_output({"whatever": 1}, {}) == []


def test_validate_pattern():
    schema = {
        "type": "object",
        "properties": {"email": {"type": "string", "pattern": "@"}},
        "required": ["email"],
    }
    assert validate_structured_output({"email": "noat"}, schema)
    assert validate_structured_output({"email": "a@b"}, schema) == []


# === 连续失败计数 ===


def test_count_consecutive_failures():
    msgs = [HumanMessage("q"), AIMessage("a"), _err_tm("1"), _err_tm("2")]
    assert count_consecutive_structured_output_failures(msgs) == 2


def test_count_resets_on_success():
    msgs = [HumanMessage("q"), _err_tm("1"), _ok_tm("2")]
    assert count_consecutive_structured_output_failures(msgs) == 0


def test_count_stops_at_human_boundary():
    msgs = [_err_tm("0"), HumanMessage("new turn"), _err_tm("1")]
    assert count_consecutive_structured_output_failures(msgs) == 1


def test_count_skips_injected_hook_reminder():
    # #8 回归：失败序列里夹着 hook 注入的 reminder（合成插话），不该被当轮边界提前
    # break，否则连续失败少计、安全阀失效。应数满 MAX。
    from lumi.agents.core.meta_message import reminder_human_message

    reminder = reminder_human_message(
        [{"type": "text", "text": "<system-reminder>note</system-reminder>"}]
    )
    msgs: list = [HumanMessage("q")]
    for i in range(MAX_CONSECUTIVE_FAILURES):
        msgs += [_err_tm(str(i)), reminder]
    assert (
        count_consecutive_structured_output_failures(msgs) == MAX_CONSECUTIVE_FAILURES
    )


def test_count_stops_at_meta_non_reminder_boundary():
    # 回归（review 发现）：后台任务通知是合成消息但**非 reminder**——它是模型要响应
    # 的新输入、构成轮边界，计数必须在此 break，不能把上一轮失败泄漏进新一轮。
    from lumi.agents.core.meta_message import synthetic_human_message

    bg = synthetic_human_message("background task done")
    msgs = [_err_tm("old1"), _err_tm("old2"), bg, _err_tm("new1")]
    assert count_consecutive_structured_output_failures(msgs) == 1


# === 共享抽象：is_internal_tool / iter_current_turn ===


def test_is_internal_tool():
    from lumi.agents.core.structured_tool import is_internal_tool

    assert is_internal_tool(TOOL)
    assert not is_internal_tool("bash")
    assert not is_internal_tool("")


def test_iter_current_turn_skips_reminder_stops_at_real_human():
    from lumi.agents.core.meta_message import (
        iter_current_turn,
        reminder_human_message,
    )

    real = HumanMessage("real user")
    reminder = reminder_human_message([{"type": "text", "text": "r"}])
    tm = _err_tm("1")
    # 旧→新：[旧轮 human, 旧 tm, 本轮起点 real, tm, reminder]
    msgs = [HumanMessage("old"), _err_tm("old"), real, tm, reminder]
    # 从尾上溯：reminder(yield) → tm(yield) → real 是真实边界，停（不含 real）
    assert list(iter_current_turn(msgs)) == [reminder, tm]


def test_iter_current_turn_stops_at_meta_non_reminder():
    from lumi.agents.core.meta_message import iter_current_turn, synthetic_human_message

    bg = synthetic_human_message("bg done")  # 合成但非 reminder = 轮边界
    tm = _err_tm("1")
    assert list(iter_current_turn([_err_tm("old"), bg, tm])) == [tm]


# === 真工具闭包 ===


async def test_structured_tool_success_returns_command():
    tool = create_structured_output_tool(SCHEMA)
    result = await tool.ainvoke(
        {
            "name": TOOL,
            "args": {"name": "Lumi", "age": 1},
            "id": "1",
            "type": "tool_call",
        }
    )
    assert isinstance(result, Command)
    assert result.update["structured_output"] == {"name": "Lumi", "age": 1}
    assert not result.goto  # 不带 goto（默认 ()），graph 自然回 CallModel
    accepted = result.update["messages"][0]
    assert isinstance(accepted, ToolMessage) and accepted.status != "error"


async def test_structured_tool_validation_failure_returns_error():
    tool = create_structured_output_tool(SCHEMA)
    # 缺 required name（字段 optional，Pydantic 放行 None，jsonschema 拦截）
    result = await tool.ainvoke(
        {"name": TOOL, "args": {"age": 1}, "id": "9", "type": "tool_call"}
    )
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "name" in result.content


# === tool_executor 集成 ===


async def test_tool_executor_structured_success(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _StructuredFakeToolNode)
    state = {"messages": [_call({"name": "Lumi"})], "output_schema": SCHEMA}
    result = await tool_executor(state, _runtime([]), {})
    assert isinstance(result, Command)
    assert result.update["structured_output"] == {"name": "Lumi"}


async def test_tool_executor_structured_enrich(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _StructuredFakeToolNode)
    state = {
        "messages": [_call({"name": "Lumi"})],
        "output_schema": SCHEMA,
        "output_enrich": [{"target": "$", "data": {"source": "lumi"}}],
    }
    result = await tool_executor(state, _runtime([]), {})
    assert result.update["structured_output"]["source"] == "lumi"


async def test_tool_executor_structured_failure_goes_normal_path(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _StructuredFakeToolNode)
    state = {"messages": [_call({"age": 1})], "output_schema": SCHEMA}
    result = await tool_executor(state, _runtime([]), {})
    # 校验失败 → 普通路径 dict，含 error ToolMessage
    assert isinstance(result, dict)
    tms = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tms and tms[0].status == "error"


# === 连续失败兜底 ===


def test_abort_below_threshold_returns_none():
    state = {"messages": [HumanMessage("q")]}
    tool_msgs = [_err_tm(str(i)) for i in range(MAX_CONSECUTIVE_FAILURES - 1)]
    assert _structured_output_abort_message(state, tool_msgs) is None


def test_abort_at_threshold_returns_aimessage():
    state = {"messages": [HumanMessage("q")]}
    tool_msgs = [_err_tm(str(i)) for i in range(MAX_CONSECUTIVE_FAILURES)]
    msg = _structured_output_abort_message(state, tool_msgs)
    assert isinstance(msg, AIMessage)


async def test_tool_executor_aborts_after_max_failures(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _StructuredFakeToolNode)
    # state 已累计 MAX-1 次失败，本轮再失败 1 次 → 触达上限强制 END
    history = [HumanMessage("q")] + [
        _err_tm(str(i)) for i in range(MAX_CONSECUTIVE_FAILURES - 1)
    ]
    state = {"messages": [*history, _call({"age": 1})], "output_schema": SCHEMA}
    result = await tool_executor(state, _runtime([]), {})
    assert isinstance(result, Command)
    assert result.goto == END
    assert isinstance(result.update["messages"][-1], AIMessage)


# === Stop hook 联动 ===


async def test_stop_hook_no_schema_passes():
    ctx = HookContext(state={"messages": []}, config={}, event="Stop", payload={})
    assert await structured_output_stop_hook(ctx) is None


async def test_stop_hook_accepted_passes():
    msgs = [HumanMessage("q"), _ok_tm("1")]
    ctx = HookContext(
        state={"messages": msgs, "output_schema": SCHEMA},
        config={},
        event="Stop",
        payload={},
    )
    assert await structured_output_stop_hook(ctx) is None


async def test_stop_hook_pulls_back_when_unfinished():
    msgs = [HumanMessage("q"), AIMessage("纯文本结束，没调工具")]
    ctx = HookContext(
        state={"messages": msgs, "output_schema": SCHEMA},
        config={},
        event="Stop",
        payload={},
    )
    result = await structured_output_stop_hook(ctx)
    assert isinstance(result, AdditionalContext)


def _reminder_msg():
    from lumi.agents.core.meta_message import reminder_human_message

    return reminder_human_message(
        [
            {
                "type": "text",
                "text": f"<system-reminder>\n{STRUCTURED_OUTPUT_REMINDER}\n</system-reminder>\n",
            }
        ]
    )


async def test_stop_hook_gives_up_after_max_pullbacks():
    # 已拉回 MAX 次但模型仍纯文本结束 → 放弃（返回 None，让 OnAgentStop 正常 END）
    msgs = [
        HumanMessage("q"),
        *[_reminder_msg()] * MAX_STOP_PULLBACKS,
        AIMessage("还是文本"),
    ]
    ctx = HookContext(
        state={"messages": msgs, "output_schema": SCHEMA},
        config={},
        event="Stop",
        payload={},
    )
    assert await structured_output_stop_hook(ctx) is None


# === Fix: 混合批次权限路由 ===


def test_is_use_tool_pure_structured_fastpaths():
    last = _call({"name": "Lumi"})
    assert is_use_tool({"messages": [last]}, _runtime([])) == "ToolExecutor"


def test_is_use_tool_mixed_structured_not_bypassed():
    last = AIMessage(
        content="",
        tool_calls=[
            {"name": TOOL, "args": {"name": "Lumi"}, "id": "1"},
            {"name": "bash", "args": {"command": "rm -rf /"}, "id": "2"},
        ],
    )
    # 混合批次不走结构化快速路径，落到正常权限评估（危险 bash → 不应自动 ToolExecutor）
    assert is_use_tool({"messages": [last]}, _runtime([])) != "ToolExecutor"


# === Fix: 可空必填字段 ===


async def test_structured_tool_nullable_required_accepts_null():
    schema = {
        "type": "object",
        "properties": {"mid": {"type": ["string", "null"]}},
        "required": ["mid"],
    }
    tool = create_structured_output_tool(schema)
    result = await tool.ainvoke(
        {"name": TOOL, "args": {"mid": None}, "id": "1", "type": "tool_call"}
    )
    assert isinstance(result, Command)
    assert result.update["structured_output"] == {"mid": None}


# === Fix: 混合 list 路径仍 enrich ===


async def test_tool_executor_mixed_list_applies_enrich(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _MixedFakeToolNode)
    state = {
        "messages": [_call({"name": "Lumi"})],
        "output_schema": SCHEMA,
        "output_enrich": [{"target": "$", "data": {"source": "lumi"}}],
    }
    result = await tool_executor(state, _runtime([]), {})
    assert isinstance(result, list)
    cmd = next(item for item in result if isinstance(item, Command))
    assert cmd.update["structured_output"]["source"] == "lumi"  # 混合路径 enrich 生效


# === Fix: 内部工具不泄漏给用户 hook ===


async def test_pretooluse_excludes_internal_structured_tool(monkeypatch):
    monkeypatch.setattr(nodes, "ToolNode", _StructuredFakeToolNode)
    captured: dict = {}

    async def spy(ctx):
        captured["names"] = ctx.payload.get("tool_names")
        captured["calls"] = ctx.payload.get("tool_calls")
        return None

    state = {"messages": [_call({"name": "Lumi"})], "output_schema": SCHEMA}
    with replace_hooks("PreToolUse", [spy]):
        await tool_executor(state, _runtime([]), {})
    assert all(n != TOOL for n in captured["names"])
    assert all(tc["name"] != TOOL for tc in captured["calls"])

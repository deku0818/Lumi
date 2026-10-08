"""workflow 引擎的脚本语义与诊断（回归）。子代理以替身图代替，只测编排层。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from lumi.agents.core.state import LumiAgentContext
from lumi.agents.core.workflow import engine as engine_mod
from lumi.agents.core.workflow.engine import WorkflowEngine, WorkflowRuntimeError


@pytest.fixture
def invoked(monkeypatch) -> list:
    """替身子代理：记录每次调用的 context，回 "ok"。"""
    calls: list = []

    class _Graph:
        async def ainvoke(self, inputs, context=None):
            calls.append(context)
            return {"messages": [AIMessage(content="ok")]}

    async def fake_get_tools(**kwargs):
        return []

    async def fake_create_agent(**kwargs):
        return SimpleNamespace(graph=_Graph()), LumiAgentContext()

    monkeypatch.setattr("lumi.agents.tools.get_tools", fake_get_tools)
    monkeypatch.setattr("lumi.agents.core.graph.create_agent", fake_create_agent)
    monkeypatch.setattr("lumi.agents.tools.load_agents", lambda **kw: [])
    return calls


async def _run(script: str, **kw):
    return await WorkflowEngine(script, **kw).run()


async def test_pipeline_keeps_default_bound_params(invoked):
    # 带默认值的形参曾被计入参数个数，item/idx 覆盖掉默认绑定（schema=S 被维度 dict 顶掉）
    out = await _run('return await pipeline(["a"], lambda d, s="S": (d, s))')
    assert out.result == [("a", "S")]


async def test_parallel_accepts_coroutines(invoked):
    # 传协程（而非 lambda）曾静默变成 [None, None]，一个子代理都没派
    out = await _run('return await parallel([agent("x"), agent("y")])')
    assert out.result == ["ok", "ok"]
    assert len(invoked) == 2


async def test_script_keeps_multiline_strings_and_line_numbers(invoked):
    # textwrap.indent 包裹曾改写多行字符串内容、报错行号偏 1
    script = 's = """line1\n  line2"""\nreturn s'
    assert (await _run(script)).result == "line1\n  line2"
    with pytest.raises(WorkflowRuntimeError, match="第 3 行"):
        await _run('log("start")\nx = {}\nreturn x["findings"]')


async def test_failure_reports_type_and_progress(invoked):
    # 失败诊断曾只剩 "Error: 'findings'"：没有异常类型、已派子代理数与日志
    script = 'log("开始")\nawait agent("a")\nreturn {}["findings"]'
    with pytest.raises(WorkflowRuntimeError) as e:
        await _run(script)
    msg = str(e.value)
    assert "KeyError" in msg and "第 3 行" in msg and "1 个子代理" in msg
    assert "开始" in msg


async def test_common_builtins_available(invoked):
    script = (
        "try:\n"
        "    [][1]\n"
        "except IndexError:\n"
        '    print("x", "y", sep="-", end="")\n'
        'return "ok"'
    )
    out = await _run(script)
    assert out.result == "ok"
    assert out.logs == ["x-y"]


async def test_unknown_agent_is_logged(invoked):
    # 未知 agent_name 在 parallel 里曾只变成 None，logs 里没有任何线索
    out = await _run('return await parallel([lambda: agent("x", agent_name="nope")])')
    assert out.result == [None]
    assert any("未找到" in line for line in out.logs)


async def test_progress_settles_when_agent_cap_hit(invoked, monkeypatch):
    # 触顶时曾在计入 total 后抛出、不计 done，进度永远到不了 100%
    monkeypatch.setattr(engine_mod, "_MAX_AGENTS", 1)
    engine = WorkflowEngine(
        'return await parallel([lambda: agent("a"), lambda: agent("b")])'
    )
    await engine.run()
    assert engine._done == engine._dispatched


def test_concurrency_is_not_bound_to_cpu_count(monkeypatch):
    # 子代理是 I/O 密集的 LLM 调用：2 核机器上曾只能 1 个并发
    monkeypatch.setattr(engine_mod.os, "cpu_count", lambda: 2)
    assert engine_mod._max_concurrency() >= 4


async def test_subagents_run_in_auto_mode(invoked):
    # workflow 子代理没有审批通道（与后台子代理同）：父为 privileged 时曾绕过分类器
    await _run(
        'return await agent("x")', parent=LumiAgentContext(tool_mode="privileged")
    )
    assert invoked[0].tool_mode == "auto"


async def test_subagents_run_at_child_depth(monkeypatch):
    # 回归：workflow 子代理入参不带 depth（恒按主 agent depth=0 跑），按 depth 区分
    # 主/子代理的闸（项目 Stop hook、goal、autoDream、shell hook 协议里的 depth）全失效
    depths: list = []

    class _Graph:
        async def ainvoke(self, inputs, context=None):
            depths.append(inputs.get("depth"))
            return {"messages": [AIMessage(content="ok")]}

    async def fake_get_tools(**kwargs):
        return []

    async def fake_create_agent(**kwargs):
        return SimpleNamespace(graph=_Graph()), LumiAgentContext()

    monkeypatch.setattr("lumi.agents.tools.get_tools", fake_get_tools)
    monkeypatch.setattr("lumi.agents.core.graph.create_agent", fake_create_agent)
    monkeypatch.setattr("lumi.agents.tools.load_agents", lambda **kw: [])

    await _run('return await agent("x")')
    await _run('return await agent("x")', depth=2)
    assert depths == [1, 2]

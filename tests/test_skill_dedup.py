"""技能仅在调用时去重，压缩后的历史不做技能专用处理。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from lumi.agents.core.meta_message import extract_text_content
from lumi.agents.core.node_helpers.skills import deduplicate_skill_results
from lumi.agents.core.nodes import preprocess_messages, tool_executor
from lumi.agents.core.preprocessing.compact import (
    build_compacted_update,
    select_for_ptl_compaction,
)
from lumi.agents.core.state import LumiAgentContext, LumiAgentState
from lumi.agents.tools.loader import SkillConfig
from lumi.agents.tools.providers.skill import render_skill, skill
from lumi.gateway.bridge import AgentBridge, build_skill_command_blocks


@pytest.fixture
def cfg(tmp_path):
    cfg = SkillConfig(
        name="probe",
        description="测试",
        prompt="完整技能正文",
        path=str(tmp_path / "probe" / "SKILL.md"),
    )
    with patch(
        "lumi.agents.core.preprocessing.skill_detector.SkillChangeDetector.get_instance",
        return_value=SimpleNamespace(peek=lambda: [cfg]),
    ):
        yield cfg


def command(cfg, content=None, *, id="command"):
    return HumanMessage(
        id=id,
        content=build_skill_command_blocks(
            cfg.name, render_skill(cfg) if content is None else content, "本次需求"
        ),
        additional_kwargs={"lumi": {"items": [{"text": "/probe 本次需求"}]}},
    )


def skill_graph():
    graph = StateGraph(LumiAgentState, context_schema=LumiAgentContext)
    graph.add_node("tools", tool_executor)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def assert_separate_resources(blocks):
    body_index = next(
        i
        for i, block in enumerate(blocks)
        if block.get("text", "").startswith("<skill-content>")
    )
    assert blocks[body_index]["text"].endswith("</skill-content>")
    assert "<skill-resources>" not in blocks[body_index]["text"]
    assert blocks[body_index + 1]["text"].startswith("<skill-resources>")
    assert blocks[body_index + 1]["text"].endswith("</skill-resources>")
    assert sum("<skill-resources>" in block.get("text", "") for block in blocks) == 1


@pytest.mark.parametrize("source", ["command", "tool"])
async def test_both_entries_reuse_either_source(cfg, source):
    full = render_skill(cfg)
    loaded = (
        command(cfg, id="loaded")
        if source == "command"
        else ToolMessage(
            content=full,
            name="skill",
            tool_call_id="old",
        )
    )
    history = [loaded, AIMessage(content="上次已完成")]

    # 斜杠入口和工具入口跨来源共用同一判断；本次用户需求仍保留。
    bridge = AgentBridge()
    captured = {}

    async def capture(blocks, **kwargs):
        captured["blocks"] = blocks
        return
        yield

    with (
        patch.object(bridge, "snapshot_messages", AsyncMock(return_value=history)),
        patch.object(bridge, "stream_response", capture),
    ):
        async for _ in bridge.stream_command("probe", "本次需求"):
            pass
    slash = extract_text_content(captured["blocks"])
    assert_separate_resources(captured["blocks"])
    runtime = SimpleNamespace(
        context=SimpleNamespace(project_dir=None),
        state={"messages": history},
    )
    result = await skill.coroutine(name="probe", runtime=runtime)
    assert "已在当前上下文中加载" in slash
    assert "已在当前上下文中加载" in result
    assert cfg.prompt not in slash + result
    assert slash.count("本次需求") == 1
    for text in (full, slash, result):
        assert text.count("<skill-resources>") == 1
        assert text.count("</skill-resources>") == 1
        assert f"`{Path(cfg.path).parent}/`" in text


def test_changed_content_or_resource_directory_loads_full(cfg):
    history = [command(cfg)]
    for changed in (
        cfg.model_copy(update={"prompt": "新版正文"}),
        cfg.model_copy(update={"path": "/other/probe/SKILL.md"}),
    ):
        assert render_skill(changed, history) == render_skill(changed)
    assert render_skill(cfg, []) == render_skill(cfg)  # 另一个会话不继承去重状态


@pytest.mark.parametrize(
    "source", ["summary", "truncated", "failed", "other_tool", "note"]
)
def test_only_complete_skill_content_counts(cfg, source):
    full = render_skill(cfg)
    messages = {
        "summary": HumanMessage(content=f"<compact>{full}</compact>"),
        "truncated": ToolMessage(content=full[:10], name="skill", tool_call_id="c"),
        "failed": ToolMessage(
            content=full, name="skill", tool_call_id="c", status="error"
        ),
        "other_tool": ToolMessage(content=full, name="read", tool_call_id="c"),
        "note": command(cfg, render_skill(cfg, [command(cfg)])),
    }
    assert render_skill(cfg, [messages[source]]) == full


@pytest.mark.parametrize("keep_full", [False, True])
async def test_compaction_keeps_short_command_and_reload_waits_for_next_call(
    cfg, keep_full
):
    loaded = command(cfg, id="loaded")
    latest = command(cfg, render_skill(cfg, [loaded]))
    history = [loaded, AIMessage(content="已完成", id="completed"), latest]
    tail = history if keep_full else [latest]
    compressed = build_compacted_update(history, tail, "之前使用过 probe")
    messages = compressed["messages"].value
    runtime = SimpleNamespace(context=LumiAgentContext(memory_enabled=False))
    result = await preprocess_messages({"messages": messages}, runtime, {})
    # 原有 hook 只注入环境等通用上下文，不自动补回技能正文。
    updated = next(m for m in reversed(result["messages"]) if m.id == messages[-1].id)
    text = extract_text_content(updated.content)
    assert cfg.prompt not in text
    assert "已在当前上下文中加载" in text
    assert text.count("本次需求") == 1
    assert "<env>" in text
    assert cfg.prompt not in extract_text_content(latest.content)  # 不修改输入对象
    assert text.count("<skill-resources>") == 1
    assert_separate_resources(updated.content)
    final = add_messages(messages, result["messages"])
    next_message = command(cfg, render_skill(cfg, final))
    assert (cfg.prompt in extract_text_content(next_message.content)) is not keep_full
    assert_separate_resources(next_message.content)


async def test_skill_tool_uses_injected_history_for_dedup(cfg):
    compiled = skill_graph()
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "c",
                "name": "skill",
                "args": {"name": "probe"},
            }
        ],
    )
    loaded = command(cfg)
    for history, expected in (([loaded], False), ([], True)):
        result = await compiled.ainvoke(
            {"messages": [*history, call]},
            context=LumiAgentContext(tools=[skill], tool_mode="privileged"),
        )
        tool_result = result["messages"][-1]
        assert tool_result.name == "skill"
        assert (cfg.prompt in tool_result.content) is expected
        assert tool_result.content.count("<skill-resources>") == 1


@pytest.mark.parametrize("already_loaded", [False, True])
async def test_parallel_skill_calls_keep_one_body_and_all_response_pairs(
    cfg, already_loaded
):
    calls = [
        {"id": "first", "name": "skill", "args": {"name": "probe"}},
        {"id": "missing", "name": "skill", "args": {"name": "missing"}},
        {"id": "invalid", "name": "skill", "args": {}},
        {"id": "second", "name": "skill", "args": {"name": "probe"}},
    ]
    history = [command(cfg), AIMessage(content="上次完成")] if already_loaded else []
    result = await skill_graph().ainvoke(
        {
            "messages": [
                *history,
                HumanMessage(content="使用技能"),
                AIMessage(content="", tool_calls=calls),
            ]
        },
        context=LumiAgentContext(tools=[skill], tool_mode="privileged"),
    )
    responses = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in responses] == [
        "first",
        "missing",
        "invalid",
        "second",
    ]
    assert sum(cfg.prompt in m.content for m in responses) == (
        0 if already_loaded else 1
    )
    assert sum("已在当前上下文中加载" in m.content for m in responses) == (
        2 if already_loaded else 1
    )
    assert "不存在" in responses[1].content
    assert responses[2].status == "error"
    for response in (responses[0], responses[-1]):
        assert response.content.count("<skill-resources>") == 1
        assert f"`{Path(cfg.path).parent}/`" in response.content


def test_batch_keeps_changed_skill_content(cfg):
    calls = [
        {"id": key, "name": "skill", "args": {"name": "probe"}}
        for key in ("first", "second")
    ]
    results = [
        ToolMessage(content=render_skill(cfg), name="skill", tool_call_id="first"),
        ToolMessage(
            content=render_skill(cfg.model_copy(update={"prompt": "更新后的正文"})),
            name="skill",
            tool_call_id="second",
        ),
    ]
    assert deduplicate_skill_results(calls, results) == results


async def test_parallel_offloaded_skills_are_not_marked_as_loaded(cfg):
    calls = [
        {"id": key, "name": "skill", "args": {"name": "probe"}}
        for key in ("first", "second")
    ]
    with patch(
        "lumi.agents.core.node_helpers.execution.get_config",
        return_value=SimpleNamespace(
            config=SimpleNamespace(
                token=SimpleNamespace(
                    once_tool_max_bytes=1,
                    round_tool_max_bytes=1,
                )
            )
        ),
    ):
        result = await skill_graph().ainvoke(
            {
                "messages": [
                    HumanMessage(content="使用技能"),
                    AIMessage(content="", tool_calls=calls),
                ]
            },
            context=LumiAgentContext(tools=[skill], tool_mode="privileged"),
        )
    responses = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in responses] == ["first", "second"]
    assert all(
        m.content.startswith("工具返回内容过大，已卸载到文件") for m in responses
    )
    assert all("已在当前上下文中加载" not in m.content for m in responses)


@pytest.mark.parametrize("source", ["tool", "command"])
async def test_ptl_keeps_skill_tool_note_and_next_call_loads_full(cfg, source):
    old_call = AIMessage(
        content="",
        tool_calls=[{"id": "old", "name": "skill", "args": {"name": "probe"}}],
    )
    full = ToolMessage(content=render_skill(cfg), name="skill", tool_call_id="old")
    previous = (
        [HumanMessage(content="上次请求"), old_call, full]
        if source == "tool"
        else [command(cfg)]
    )
    note = render_skill(cfg, previous)
    current = HumanMessage(content="再次使用 probe 技能检查代码")
    skill_call = AIMessage(
        content="",
        tool_calls=[{"id": "current", "name": "skill", "args": {"name": "probe"}}],
    )
    skill_result = ToolMessage(content=note, name="skill", tool_call_id="current")
    read_call = AIMessage(
        content="", tool_calls=[{"id": "read", "name": "read", "args": {}}]
    )
    read_result = ToolMessage(content="文件内容", name="read", tool_call_id="read")
    history = [
        *previous,
        AIMessage(content="上次完成", id="previous-complete"),
        current,
        skill_call,
        skill_result,
        read_call,
        read_result,
    ]
    history = [
        m if m.id else m.model_copy(update={"id": f"message-{i}"})
        for i, m in enumerate(history)
    ]
    _, tail = select_for_ptl_compaction(history)
    messages = build_compacted_update(history, tail, "之前使用过 probe 技能")[
        "messages"
    ].value
    runtime = SimpleNamespace(context=LumiAgentContext())
    update = await preprocess_messages(
        {"messages": messages, "ptl_retry": True}, runtime, {}
    )
    processed = add_messages(messages, update["messages"])
    assert all(cfg.prompt not in str(m.content) for m in processed)
    tools = [m for m in processed if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tools] == ["current", "read"]
    assert tools[0].content == note
    assert tools[1].content == "文件内容"
    # 明确再次调用 skill 工具，才会重新加载全文并走正常工具输出预算。
    next_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "reload",
                "name": "skill",
                "args": {"name": "probe"},
            }
        ],
    )
    result = await skill_graph().ainvoke(
        {"messages": [*processed, next_call]},
        context=LumiAgentContext(tools=[skill], tool_mode="privileged"),
    )
    assert result["messages"][-1].content == render_skill(cfg)
    assert result["messages"][-1].tool_call_id == "reload"
    assert result["messages"][-1].content.count("<skill-resources>") == 1
    assert skill_result.content == note

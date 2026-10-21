"""Use real SDK options/message types; substitute only the networked SDK client."""

from dataclasses import replace

import claude_agent_sdk as sdk
import pytest
from claude_agent_sdk.types import StreamEvent

from lumi.agents.permissions.models import PermissionDecision
from lumi.agents.runtime.subagents.claude import ClaudeExecutor
from lumi.agents.runtime.subagents.types import (
    Subagent,
    SubagentQuestionError,
    SubagentTurn,
)


@pytest.fixture
def agent(tmp_path):
    return Subagent(
        "subagent_1",
        "parent",
        str(tmp_path),
        "claude_code",
        "claude_code",
        {"name": "claude_code"},
        1,
        "",
        "default",
        {},
        0,
    )


def result(**kwargs):
    return sdk.ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="native-42",
        result="answer",
        **kwargs,
    )


def turn():
    return SubagentTurn(
        "bg_1", "subagent_1", 1, "question", "running", True, "", "", 0, None
    )


def client(monkeypatch, messages, inspect=None):
    monkeypatch.setattr(
        "lumi.agents.runtime.subagents.claude.resolve_cli", lambda _: "/native/claude"
    )

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def query(self, prompt):
            assert prompt == "question"
            if inspect:
                await inspect(self.options)

        async def receive_messages(self):
            for message in messages:
                yield message

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)


async def test_native_cli_options_partial_events_and_resume(agent, monkeypatch):
    options_seen = []

    async def inspect(options):
        options_seen.append(options)

    client(
        monkeypatch,
        [
            sdk.SystemMessage("init", {"session_id": "native-42"}),
            StreamEvent(
                "e1",
                "native-42",
                {"delta": {"type": "text_delta", "text": "answer"}},
                "nested-tool",
            ),
            sdk.AssistantMessage([sdk.TextBlock("answer")], "native-model"),
            sdk.AssistantMessage(
                [sdk.ToolUseBlock("t1", "Read", {"file_path": "x"})], "native-model"
            ),
            sdk.UserMessage([sdk.ToolResultBlock("t1", "contents")]),
            result(origin={"kind": "task-notification"}),
            result(),
        ],
        inspect,
    )
    handle, events = {}, []
    executor = ClaudeExecutor()
    assert (
        await executor.run(
            agent, turn(), lambda k, p: events.append((k, p)), handle.update
        )
        == "answer"
    )
    assert handle == {"session_id": "native-42"}
    assert (
        await executor.run(
            replace(agent, handle=handle), turn(), lambda *_: None, handle.update
        )
        == "answer"
    )
    assert options_seen[0].cli_path == "/native/claude"
    assert all(
        options.permission_mode == "bypassPermissions" for options in options_seen
    )
    assert all(options.sandbox == {"enabled": False} for options in options_seen)
    assert options_seen[0].resume is None and options_seen[1].resume == "native-42"
    assert options_seen[0].system_prompt == {"type": "preset", "preset": "claude_code"}
    assert (
        "message.delta",
        {"text": "answer", "parent_tool_use_id": "nested-tool"},
    ) in events
    assert any(k == "tool.complete" and p["output"] == "contents" for k, p in events)


@pytest.mark.parametrize("decision", list(PermissionDecision))
async def test_full_access_allows_tools_except_explicit_project_denials(
    agent, monkeypatch, decision
):
    monkeypatch.setattr(
        "lumi.agents.permissions.engine.PermissionEngine.evaluate", lambda *_: decision
    )

    async def inspect(options):
        hook = options.hooks["PreToolUse"][0].hooks[0]
        response = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "echo x"}}, "tool-1", {}
        )
        permission = await options.can_use_tool("Bash", {"command": "echo x"}, {})
        if decision == PermissionDecision.DENY:
            assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
            assert isinstance(permission, sdk.PermissionResultDeny)
        else:
            assert response == {"continue_": True}
            assert isinstance(permission, sdk.PermissionResultAllow)

    client(monkeypatch, [result()], inspect)
    assert (
        await ClaudeExecutor().run(agent, turn(), lambda *_: None, lambda _: None)
        == "answer"
    )


@pytest.mark.parametrize("has_result", [True, False])
async def test_native_question_is_handed_back_with_options_and_session(
    agent, monkeypatch, has_result
):
    monkeypatch.setattr(
        "lumi.agents.permissions.engine.PermissionEngine.evaluate",
        lambda *_: PermissionDecision.ALLOW,
    )

    async def inspect(options):
        hook = options.hooks["PreToolUse"][0].hooks[0]
        response = await hook(
            {"tool_name": "AskUserQuestion", "tool_input": {"questions": questions}},
            "question",
            {},
        )
        assert response["continue_"] is False
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert isinstance(
            await options.can_use_tool("AskUserQuestion", {"questions": questions}, {}),
            sdk.PermissionResultDeny,
        )

    questions = [
        {
            "question": "选择哪种格式？",
            "header": "格式",
            "options": [
                {"label": "CSV", "description": "表格"},
                {"label": "JSON", "description": "结构化"},
            ],
            "multiSelect": False,
        }
    ]
    client(
        monkeypatch,
        [result()]
        if has_result
        else [sdk.SystemMessage("init", {"session_id": "native-42"})],
        inspect,
    )
    handle = {}
    with pytest.raises(SubagentQuestionError) as error:
        await ClaudeExecutor().run(agent, turn(), lambda *_: None, handle.update)
    assert error.value.questions == questions
    assert handle == {"session_id": "native-42"}

    async def inspect_resume(options):
        assert options.resume == "native-42"

    client(monkeypatch, [result()], inspect_resume)
    assert (
        await ClaudeExecutor().run(
            replace(agent, handle=handle), turn(), lambda *_: None, handle.update
        )
        == "answer"
    )


@pytest.mark.parametrize(
    "messages",
    [
        [result(terminal_reason="aborted_tools")],
        [sdk.ResultMessage("error_max_turns", 1, 1, True, 1, "native-42")],
        [],
        [sdk.SystemMessage("init", {"session_id": "different"})],
    ],
)
async def test_failure_missing_completion_and_wrong_session(
    agent, monkeypatch, messages
):
    client(monkeypatch, messages)
    with pytest.raises(RuntimeError):
        await ClaudeExecutor().run(
            replace(agent, handle={"session_id": "native-42"}),
            turn(),
            lambda *_: None,
            lambda _: None,
        )

"""Claude Code's official Python SDK, resuming its own persistent session."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .executables import resolve_cli
from .types import (
    Emit,
    SaveHandle,
    Subagent,
    SubagentExecutor,
    SubagentQuestionError,
    SubagentTurn,
)


def _plain(message: Any) -> Any:
    if is_dataclass(message):
        return asdict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump()
    return str(message)


class ClaudeExecutor(SubagentExecutor):
    async def run(
        self, agent: Subagent, turn: SubagentTurn, emit: Emit, save_handle: SaveHandle
    ) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            HookMatcher,
            PermissionResultAllow,
            PermissionResultDeny,
            ResultMessage,
            SystemMessage,
            UserMessage,
        )
        from claude_agent_sdk.types import StreamEvent

        from lumi.agents.permissions.engine import PermissionEngine
        from lumi.agents.permissions.models import PermissionDecision

        engine = PermissionEngine(Path(agent.workspace))
        questions: list[dict] = []
        tool_names: dict[str, str] = {}
        names = {
            "Bash": "bash",
            "Read": "read",
            "Write": "write",
            "Edit": "edit",
            "Glob": "glob",
            "Grep": "grep",
        }

        def denied_reason(name: str, args: dict) -> str | None:
            engine.reload()
            decision = engine.evaluate(names.get(name, name), args)
            if decision == PermissionDecision.DENY:
                emit("permission.denied", {"name": name, "args": args})
                return "Lumi 项目权限规则拒绝此调用"
            if name == "AskUserQuestion":
                for question in args.get("questions") or []:
                    if question not in questions:
                        questions.append(question)
                reason = "问题已转交主 Agent，等待回答后继续当前子会话"
                emit(
                    "approval.request",
                    {"name": name, "args": args, "status": "blocked"},
                )
                return reason
            return None

        async def pre_tool(data: dict, tool_use_id: str | None, context: Any) -> dict:
            reason = denied_reason(
                data.get("tool_name", ""), data.get("tool_input") or {}
            )
            if reason:
                return {
                    **({"continue_": False, "stopReason": reason} if questions else {}),
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    },
                }
            return {"continue_": True}

        async def can_use_tool(name: str, args: dict, context: Any) -> Any:
            if reason := denied_reason(name, args):
                return PermissionResultDeny(message=reason)
            return PermissionResultAllow(updated_input=args)

        command = agent.definition.get("command")
        if command and len(command) != 1:
            raise ValueError("Claude Code command 只接受可执行文件路径")
        cli_path = command[0] if command else resolve_cli("claude")
        options: dict[str, Any] = {
            "cwd": agent.workspace,
            "include_partial_messages": True,
            "cli_path": cli_path,
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "permission_mode": "bypassPermissions",
            "sandbox": {"enabled": False},
            "can_use_tool": can_use_tool,
            "hooks": {"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool])]},
            "setting_sources": ["user", "project", "local"],
        }
        if model := agent.definition.get("model"):
            options["model"] = model
        if native_id := agent.handle.get("session_id"):
            options["resume"] = native_id
        if tools := agent.definition.get("tools"):
            options["tools"] = tools

        async with ClaudeSDKClient(options=ClaudeAgentOptions(**options)) as client:
            await client.query(turn.prompt)
            async for message in client.receive_messages():
                if isinstance(message, SystemMessage):
                    data = message.data
                    if session_id := data.get("session_id"):
                        if native_id and native_id != session_id:
                            raise RuntimeError("Claude Code 恢复了不同会话，拒绝继续")
                        save_handle({"session_id": session_id})
                    emit("native.event", {"subtype": message.subtype, "data": data})
                elif isinstance(message, StreamEvent):
                    event = message.event
                    delta = event.get("delta") or {}
                    if delta.get("type") in ("text_delta", "thinking_delta"):
                        thinking = delta["type"] == "thinking_delta"
                        emit(
                            "thinking.delta" if thinking else "message.delta",
                            {
                                "text": delta.get(
                                    "thinking" if thinking else "text", ""
                                ),
                                "parent_tool_use_id": message.parent_tool_use_id,
                            },
                        )
                    else:
                        emit(
                            "native.event",
                            {
                                "event": event,
                                "parent_tool_use_id": message.parent_tool_use_id,
                            },
                        )
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        value = _plain(block)
                        if (
                            isinstance(value, dict)
                            and "name" in value
                            and "input" in value
                        ):
                            tool_names[value.get("id", "")] = value["name"]
                            emit(
                                "tool.start",
                                {
                                    "name": value["name"],
                                    "args": value["input"],
                                    "tool_call_id": value.get("id", ""),
                                    "parent_tool_use_id": message.parent_tool_use_id,
                                },
                            )
                    emit("message.complete", {"message": _plain(message)})
                elif isinstance(message, ResultMessage):
                    if message.origin and message.origin.get("kind") != "human":
                        emit("native.event", {"message": _plain(message)})
                        continue
                    if native_id and message.session_id != native_id:
                        raise RuntimeError("Claude Code 返回了不同会话，拒绝保存")
                    save_handle({"session_id": message.session_id})
                    if questions:
                        raise SubagentQuestionError(questions)
                    if (
                        message.is_error
                        or message.subtype != "success"
                        or message.terminal_reason not in (None, "completed")
                    ):
                        detail = message.result or "; ".join(message.errors or [])
                        raise RuntimeError(
                            f"Claude Code 本轮失败: {message.subtype} ({message.terminal_reason}): {detail}"
                        )
                    emit("usage", {"usage": message.usage})
                    return message.result or ""
                else:
                    if isinstance(message, UserMessage) and isinstance(
                        message.content, list
                    ):
                        for block in message.content:
                            value = _plain(block)
                            if isinstance(value, dict) and "tool_use_id" in value:
                                tool_id = value["tool_use_id"]
                                emit(
                                    "tool.complete",
                                    {
                                        "tool_call_id": tool_id,
                                        "name": tool_names.get(tool_id, ""),
                                        "output": value.get("content"),
                                        "is_error": bool(value.get("is_error")),
                                        "parent_tool_use_id": message.parent_tool_use_id,
                                    },
                                )
                    emit("native.event", {"message": _plain(message)})
        if questions:
            raise SubagentQuestionError(questions)
        raise RuntimeError("Claude Code 未返回本轮完成事件")

    async def close(self) -> None:
        # Each run owns its SDK scope. Cancellation drains/closes it in the same task.
        pass

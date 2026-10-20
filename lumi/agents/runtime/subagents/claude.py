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
    SubagentBlockedError,
    SubagentExecutor,
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
        blocked: list[str] = []
        tool_names: dict[str, str] = {}
        names = {
            "Bash": "bash",
            "Read": "read",
            "Write": "write",
            "Edit": "edit",
            "Glob": "glob",
            "Grep": "grep",
        }

        async def pre_tool(data: dict, tool_use_id: str | None, context: Any) -> dict:
            name = data.get("tool_name", "")
            args = data.get("tool_input") or {}
            engine.reload()
            decision = engine.evaluate(names.get(name, name), args)
            if (
                decision in (PermissionDecision.DENY, PermissionDecision.ASK)
                or name == "AskUserQuestion"
            ):
                if decision != PermissionDecision.DENY:
                    blocked.append(f"Claude Code 需要审批或输入: {name}")
                emit("permission.denied", {"name": name, "args": args})
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Lumi 项目权限规则拒绝此调用",
                    }
                }
            return {"continue_": True}

        async def can_use_tool(name: str, args: dict, context: Any) -> Any:
            engine.reload()
            decision = engine.evaluate(names.get(name, name), args)
            permitted = decision == PermissionDecision.ALLOW or (
                decision == PermissionDecision.UNMATCHED
                and (
                    agent.tool_mode == "privileged"
                    or agent.tool_mode == "accept_edits"
                    and name in ("Write", "Edit")
                )
            )
            if permitted and name != "AskUserQuestion":
                return PermissionResultAllow(updated_input=args)
            reason = f"Claude Code 需要审批或输入: {name}"
            if decision != PermissionDecision.DENY:
                blocked.append(reason)
                emit(
                    "approval.request",
                    {"name": name, "args": args, "status": "blocked"},
                )
            return PermissionResultDeny(message=reason)

        command = agent.definition.get("command")
        if command and len(command) != 1:
            raise ValueError("Claude Code command 只接受可执行文件路径")
        cli_path = command[0] if command else resolve_cli("claude")
        options: dict[str, Any] = {
            "cwd": agent.workspace,
            "include_partial_messages": True,
            "cli_path": cli_path,
            "system_prompt": {"type": "preset", "preset": "claude_code"},
            "permission_mode": "default",
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
                    if blocked:
                        raise SubagentBlockedError("; ".join(blocked))
                    if (
                        message.is_error
                        or message.subtype != "success"
                        or message.terminal_reason not in (None, "completed")
                    ):
                        detail = message.result or "; ".join(message.errors or [])
                        raise RuntimeError(
                            f"Claude Code 本轮失败: {message.subtype} ({message.terminal_reason}): {detail}"
                        )
                    save_handle({"session_id": message.session_id})
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
        raise RuntimeError("Claude Code 未返回本轮完成事件")

    async def close(self) -> None:
        # Each run owns its SDK scope. Cancellation drains/closes it in the same task.
        pass

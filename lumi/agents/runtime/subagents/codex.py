"""Codex app-server's bidirectional stdio protocol; no terminal text parsing."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from typing import Any

from lumi.agents.runtime.bg_process import terminate_group
from lumi.utils.constants import GRACEFUL_SHUTDOWN_TIMEOUT

from .executables import resolve_cli
from .types import (
    Emit,
    SaveHandle,
    Subagent,
    SubagentBlockedError,
    SubagentDeliveryUnknownError,
    SubagentExecutor,
    SubagentQuestionError,
    SubagentTurn,
)


class CodexExecutor(SubagentExecutor):
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue[dict | Exception] = asyncio.Queue()
        self._next_id = 0
        self._thread_id = ""
        self._emit: Emit = lambda *_: None
        self._blocked = ""
        self._active = False

    async def _open(self, agent: Subagent, save_handle: SaveHandle) -> None:
        command = agent.definition.get("command") or [resolve_cli("codex")]
        self.process = await asyncio.create_subprocess_exec(
            *command,
            "app-server",
            cwd=agent.workspace,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            start_new_session=os.name != "nt",
        )
        self._events = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._drain_stderr())
        await self._request(
            "initialize",
            {
                "clientInfo": {"name": "lumi", "title": "Lumi", "version": "1"},
            },
        )
        self._write({"method": "initialized"})
        options: dict[str, Any] = {
            "cwd": agent.workspace,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if prompt := agent.definition.get("system_prompt"):
            options["developerInstructions"] = prompt
        if model := agent.definition.get("model"):
            options["model"] = model
        if native_id := agent.handle.get("thread_id"):
            result = await self._request(
                "thread/resume", {**options, "threadId": native_id}
            )
            if result["thread"]["id"] != native_id:
                raise RuntimeError("Codex 恢复了不同的会话，拒绝继续")
        else:
            result = await self._request("thread/start", options)
        self._thread_id = result["thread"]["id"]
        save_handle({"thread_id": self._thread_id})

    def _write(self, message: dict) -> None:
        if self.process is None or self.process.returncode is not None:
            raise ConnectionError("Codex app-server 已退出")
        self.process.stdin.write((json.dumps(message) + "\n").encode())

    async def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 30)
        finally:
            self._pending.pop(request_id, None)

    async def _read(self) -> None:
        try:
            while line := await self.process.stdout.readline():
                record = json.loads(line)
                if "method" in record:
                    if "id" in record:
                        # Full access avoids normal approvals; actual interaction still has no UI.
                        await self._answer_request(record)
                    elif self._active:
                        self._events.put_nowait(record)
                    elif self._thread_id:
                        self._emit("native.event", record)
                elif future := self._pending.get(record.get("id")):
                    if not future.done():
                        if "error" in record:
                            future.set_exception(RuntimeError(str(record["error"])))
                        else:
                            future.set_result(record.get("result") or {})
            raise ConnectionError("Codex app-server 连接已结束")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            self._events.put_nowait(error)

    async def _answer_request(self, record: dict) -> None:
        method = record["method"]
        params = record.get("params") or {}
        if method == "item/tool/requestUserInput":
            # Keep the native request unanswered until the owning run interrupts it.
            # Calling _request here would deadlock the reader on its own response.
            self._events.put_nowait(record)
            return
        self._blocked = f"Codex 需要原生交互: {method}"
        self._emit(
            "approval.request",
            {"native_method": method, "request": params, "status": "blocked"},
        )
        if method.endswith("requestApproval"):
            if method == "item/permissions/requestApproval":
                result = {"permissions": {}, "scope": "turn"}
            else:
                result = {"decision": "decline"}
            self._write({"id": record["id"], "result": result})
        else:
            self._write(
                {
                    "id": record["id"],
                    "error": {
                        "code": -32601,
                        "message": "Lumi 子代理只读入口暂不支持此原生交互",
                    },
                }
            )

    async def _drain_stderr(self) -> None:
        while await self.process.stderr.read(8192):
            pass

    async def run(
        self, agent: Subagent, turn: SubagentTurn, emit: Emit, save_handle: SaveHandle
    ) -> str:
        self._emit = emit
        self._blocked = ""
        if self.process is None or self.process.returncode is not None:
            await self.close()
            await self._open(agent, save_handle)
        # Startup/replay notifications belong to existing history, not this new turn.
        while not self._events.empty():
            event = self._events.get_nowait()
            if isinstance(event, Exception):
                raise event
        self._active = True
        try:
            result = await self._request(
                "turn/start",
                {
                    "threadId": self._thread_id,
                    "input": [{"type": "text", "text": turn.prompt}],
                },
            )
        except (ConnectionError, TimeoutError, BrokenPipeError) as error:
            await self.close()
            raise SubagentDeliveryUnknownError(
                "Codex 未确认是否接收本轮，未自动重发"
            ) from error
        native_turn_id = result["turn"]["id"]
        messages: dict[str, str] = {}
        final_messages: dict[str, str] = {}
        try:
            while True:
                event = await self._events.get()
                if isinstance(event, Exception):
                    raise SubagentDeliveryUnknownError(
                        "Codex 执行连接中断，完成状态未确认"
                    ) from event
                params = event.get("params") or {}
                if params.get("threadId", self._thread_id) != self._thread_id:
                    emit("native.event", {"method": event["method"], **params})
                    continue
                if params.get("turnId", native_turn_id) != native_turn_id:
                    continue
                method = event["method"]
                if method == "item/tool/requestUserInput":
                    questions = params.get("questions") or []
                    # Do not give Codex an empty answer and let it continue guessing.
                    # Always hand back the captured questions, even if interrupt fails;
                    # the outer cleanup closes only this executor's owned app-server.
                    with suppress(Exception):
                        async with asyncio.timeout(GRACEFUL_SHUTDOWN_TIMEOUT):
                            await self._request(
                                "turn/interrupt",
                                {"threadId": self._thread_id, "turnId": native_turn_id},
                            )
                    raise SubagentQuestionError(questions)
                elif method == "item/agentMessage/delta":
                    emit(
                        "message.delta",
                        {
                            "text": params.get("delta", ""),
                            "message_id": params.get("itemId", ""),
                        },
                    )
                elif method in ("item/started", "item/completed"):
                    item = params.get("item") or {}
                    item_type = item.get("type")
                    if item_type == "agentMessage":
                        if method == "item/completed":
                            messages[item["id"]] = item.get("text", "")
                            if item.get("phase") == "final_answer":
                                final_messages[item["id"]] = item.get("text", "")
                        emit(
                            "message.start"
                            if method == "item/started"
                            else "message.complete",
                            {"message_id": item["id"], "text": item.get("text", "")},
                        )
                    elif item_type == "reasoning":
                        emit("native.event", {"method": method, **params})
                    elif item_type != "userMessage":
                        emit(
                            "tool.start"
                            if method == "item/started"
                            else "tool.complete",
                            {
                                "name": item_type,
                                "tool_call_id": item.get("id", ""),
                                "native_item": item,
                            },
                        )
                elif method == "turn/completed":
                    native_turn = params.get("turn") or {}
                    if native_turn.get("id") != native_turn_id:
                        continue
                    if self._blocked:
                        raise SubagentBlockedError(self._blocked)
                    if native_turn.get("status") != "completed":
                        raise RuntimeError(f"Codex 本轮未完成: {native_turn}")
                    return "\n".join((final_messages or messages).values())
                else:
                    emit("native.event", {"method": method, **params})
        except BaseException:
            await self.close()
            raise
        finally:
            self._active = False

    async def close(self) -> None:
        if self.process is not None:
            await terminate_group(self.process)
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self.process = None
        self._reader = self._stderr = None
        self._thread_id = ""

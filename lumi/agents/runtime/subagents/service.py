"""One owner per subagent: persisted FIFO turns, executor lifetime and result delivery."""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import Callable
from pathlib import Path

from lumi.agents.runtime.bg_tasks import (
    BackgroundTaskEntry,
    TaskKind,
    TaskStatus,
    compose_notification_hint,
    get_task_registry,
)
from lumi.sessions.subagents import SubagentStore
from lumi.utils.file_lock import lock_exclusive
from lumi.utils.logger import logger

from .types import (
    TERMINAL_STATUSES,
    Subagent,
    SubagentBlockedError,
    SubagentDeliveryUnknownError,
    SubagentExecutor,
    SubagentQuestionError,
    SubagentTurn,
)


def default_executor(kind: str) -> SubagentExecutor:
    if kind == "lumi":
        from .lumi import LumiExecutor

        return LumiExecutor()
    if kind == "codex":
        from .codex import CodexExecutor

        return CodexExecutor()
    if kind == "claude_code":
        from .claude import ClaudeExecutor

        return ClaudeExecutor()
    raise ValueError(f"不支持的子代理执行器: {kind}")


RESULT_NOTIFICATION_PREFIX = "子代理本轮结果（需要继续沟通时使用 agent_message）：\n"


def question_handoff(result: dict) -> dict:
    """The same instructions accompany synchronous results and durable notifications."""
    if not result.get("questions"):
        return result
    return {
        **result,
        "next_action": (
            "子代理需要你回答 questions。先根据主会话上下文、已知事实或可验证的信息回答；"
            "仅当缺少真正属于用户的决策时，使用 ask 向用户提问。获得答案后，"
            "用 agent_message 把问题和答案发给本结果的同一 agent_id，继续原任务。"
            "不要重新创建实例，不要把未回答或取消的问题当成已有答案。"
        ),
    }


def result_notification(delivery: dict) -> str:
    """JSON keeps provider output inside data, including any XML-looking output."""
    content = {
        key: delivery[key]
        for key in (
            "delivery_id",
            "agent_id",
            "name",
            "executor",
            "turn_id",
            "ordinal",
            "status",
            "output",
            "error",
        )
    }
    if delivery.get("questions"):
        content["questions"] = delivery["questions"]
    return RESULT_NOTIFICATION_PREFIX + json.dumps(
        question_handoff(content), ensure_ascii=False
    )


class TimelineWriter:
    """Coalesce text deltas before committing and broadcasting; never drop final output."""

    def __init__(self, service: SubagentService, agent_id: str, turn_id: str) -> None:
        self.service, self.agent_id, self.turn_id = service, agent_id, turn_id
        self.pending: tuple[str, dict] | None = None
        self.last_flush = time.monotonic()
        self.active_tools: dict[str, str] = {}
        self.completed_tools: set[str] = set()

    def emit(self, kind: str, payload: dict) -> None:
        if kind in ("message.delta", "thinking.delta"):
            context = {k: v for k, v in payload.items() if k != "text"}
            if self.pending:
                previous_kind, previous = self.pending
                if previous_kind != kind or context != {
                    k: v for k, v in previous.items() if k != "text"
                }:
                    self.flush()
            if self.pending:
                self.pending[1]["text"] += payload.get("text", "")
            else:
                self.pending = (kind, dict(payload))
            if (
                len(self.pending[1].get("text", "")) < 512
                and time.monotonic() - self.last_flush < 0.15
            ):
                return
            self.flush()
        else:
            self.flush()
            self._write(kind, payload)

    def _write(self, kind: str, payload: dict) -> None:
        event = self.service.store.event(self.agent_id, self.turn_id, kind, payload)
        if kind in ("tool.start", "tool.complete"):
            tool_id = payload.get("tool_call_id", "")
            if kind == "tool.start":
                self.active_tools[tool_id] = payload.get("name", "")
            else:
                self.active_tools.pop(tool_id, None)
                self.completed_tools.add(tool_id)
            self.service._registry.notify_progress(
                self.turn_id,
                {
                    "agent_id": self.agent_id,
                    "turn_id": self.turn_id,
                    "phase": "running",
                    "executor": self.service.store.agent(self.agent_id).executor,
                    "tool": ", ".join(self.active_tools.values())[:500] or None,
                    "tools_done": len(self.completed_tools),
                },
            )
        if self.service.on_event:
            try:
                self.service.on_event(self.agent_id, event)
            except Exception:
                logger.exception("子代理观察者失败，执行继续")

    def flush(self) -> None:
        if self.pending:
            kind, payload = self.pending
            self.pending = None
            self._write(kind, payload)
            self.last_flush = time.monotonic()


class SubagentService:
    def __init__(
        self,
        path: Path,
        executor_factory: Callable[[str], SubagentExecutor] = default_executor,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = path.with_suffix(".lock").open("a+")
        try:
            lock_exclusive(self._lock_file)
        except OSError as error:
            self._lock_file.close()
            raise RuntimeError("已有后端进程持有 subagent 运行时") from error
        try:
            self.store = SubagentStore(path)
            self.store.recover()
        except Exception:
            self._lock_file.close()
            raise
        self.output_dir = path.parent / "subagent_outputs"
        self.output_dir.mkdir(mode=0o700, exist_ok=True)
        self.executor_factory = executor_factory
        self.on_event: Callable[[str, dict], None] | None = None
        self._turn_tasks: dict[str, asyncio.Task] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._executors: dict[str, SubagentExecutor] = {}
        self._finished: dict[str, asyncio.Event] = {}
        # Limit each delegation layer independently: synchronous parents must not consume
        # all slots needed by their subagents.
        self._slots: dict[int, asyncio.Semaphore] = {}
        self._closed = False
        self._retiring: set[str] = set()
        self._started = False
        self._registry = get_task_registry()
        self._registry.add_notification_handler(self._background_complete)

    def start(self) -> None:
        """Activate durable notifications at runtime startup, never from metadata reads."""
        if self._started:
            return
        self._started = True
        # Existing notification turns must acquire their FIFO positions before
        # forwarding pending results can append and schedule newer turns.
        for turn in self.store.queued_notifications():
            self._schedule(turn)
        for agent_id in self.store.agent_ids():
            self._forward_results(agent_id)

    def _background_complete(
        self, entry: BackgroundTaskEntry, notification: str
    ) -> bool:
        owner = self.store.agent(entry.thread_id)
        if owner is None:
            return False
        if self._closed or owner.agent_id in self._retiring:
            return True
        turn = self.store.submit(
            owner.agent_id,
            compose_notification_hint([notification]),
            True,
            request_key="background:" + entry.task_id,
        )
        self.start()
        self._schedule(turn)
        return True

    def _forward_results(self, owner_id: str) -> None:
        if self._closed or owner_id in self._retiring:
            return
        for delivery in self.store.pending_deliveries(owner_id):
            turn = self.store.submit(
                owner_id,
                result_notification(delivery),
                True,
                request_key="return:" + delivery["delivery_id"],
            )
            self.store.acknowledge(owner_id, [delivery["delivery_id"]])
            self._schedule(turn)

    def create(
        self,
        parent_id: str,
        workspace: str,
        definition: dict,
        depth: int,
        prompt: str,
        notify: bool = True,
        request_key: str | None = None,
        env_extra: str = "",
        tool_mode: str = "default",
    ) -> dict:
        self._validate_input(parent_id, workspace, prompt)
        self.start()
        turn = self.store.create(
            parent_id,
            str(Path(workspace).resolve()),
            definition,
            depth,
            env_extra,
            tool_mode,
            prompt,
            notify,
            request_key,
        )
        return self._schedule(turn)

    def message(
        self,
        parent_id: str,
        agent_id: str,
        prompt: str,
        notify: bool = True,
        request_key: str | None = None,
    ) -> dict:
        agent = self.store.owned_agent(parent_id, agent_id)
        self._validate_input(parent_id, agent.workspace, prompt)
        self.start()
        turn = self.store.submit(agent_id, prompt, notify, request_key)
        return self._schedule(turn)

    def _validate_input(self, parent_id: str, workspace: str, prompt: str) -> None:
        if self._closed:
            raise RuntimeError("子代理运行时已关闭")
        if parent_id in self._retiring:
            raise RuntimeError("父会话正在删除")
        if not parent_id:
            raise ValueError("子代理需要父会话 thread_id")
        if not prompt.strip():
            raise ValueError("子代理消息不能为空")
        if not Path(workspace).is_dir():
            raise ValueError("子代理工作目录不存在")

    def _schedule(self, turn: SubagentTurn) -> dict:
        self._publish_latest(turn.agent_id)
        if turn.status not in TERMINAL_STATUSES:
            self._register_card(turn)
            if turn.turn_id not in self._turn_tasks:
                task = asyncio.create_task(
                    self._execute_turn(turn),
                    context=contextvars.Context(),
                    name=f"subagent:{turn.agent_id}:{turn.turn_id}",
                )
                self._turn_tasks[turn.turn_id] = task
                task.add_done_callback(lambda _: self._turn_done(turn.turn_id))
            self._registry.get(turn.turn_id).async_task = self._turn_tasks[turn.turn_id]
        return self.receipt(turn.turn_id)

    def _turn_done(self, turn_id: str) -> None:
        # Cancellation before the coroutine's first step does not enter its finally block.
        if self._turn_tasks.pop(turn_id, None) is None:
            return
        turn = self.store.turn(turn_id)
        if turn.status not in TERMINAL_STATUSES and not (
            self._closed and self.store.is_notification(turn_id)
        ):
            self._finish(turn, "cancelled", error="本轮在开始前已停止")

    def receipt(self, turn_id: str) -> dict:
        turn = self.store.turn(turn_id)
        return question_handoff(
            {
                "agent_id": turn.agent_id,
                "turn_id": turn.turn_id,
                "task_id": turn.turn_id,
                "ordinal": turn.ordinal,
                "status": turn.status,
                "output": turn.output,
                "error": turn.error,
                **self.store.question_details(turn),
            }
        )

    async def wait(self, turn_id: str) -> dict:
        if self.store.turn(turn_id).status not in TERMINAL_STATUSES:
            event = self._finished.setdefault(turn_id, asyncio.Event())
            await event.wait()
        return self.receipt(turn_id)

    def list_agents(self, parent_id: str) -> list[dict]:
        return self.store.list_agents(parent_id)

    def history(
        self,
        parent_id: str,
        agent_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> dict:
        agent = self.store.owned_agent(parent_id, agent_id)
        events = self.store.history(agent_id, after, limit)
        return {
            "agent_id": agent_id,
            "executor": agent.executor,
            "native_session_id": agent.handle.get("thread_id")
            or agent.handle.get("session_id"),
            "events": events,
            "cursor": events[-1]["seq"] if events else after,
        }

    def _register_card(self, turn: SubagentTurn) -> None:
        registry = self._registry
        if registry.get(turn.turn_id):
            return
        agent = self.store.agent(turn.agent_id)
        registry.register(
            BackgroundTaskEntry(
                task_id=turn.turn_id,
                kind=TaskKind.AGENT,
                status=TaskStatus.RUNNING,
                label=f"agent:{agent.name}",
                agent_name=agent.name,
                subagent_id=agent.agent_id,
                started_at=turn.created_at,
                output_file=self.output_dir / f"{turn.turn_id}.txt",
                prompt=turn.prompt,
                thread_id=agent.parent_id,
                progress={
                    "agent_id": agent.agent_id,
                    "turn_id": turn.turn_id,
                    "executor": agent.executor,
                    "phase": "queued",
                },
            )
        )

    async def _execute_turn(self, turn: SubagentTurn) -> None:
        try:
            lock = self._agent_locks.setdefault(turn.agent_id, asyncio.Lock())
            async with lock:
                agent = self.store.agent(turn.agent_id)
                slot = self._slots.setdefault(agent.depth, asyncio.Semaphore(4))
                async with slot:
                    await self._run(agent, turn)
        except asyncio.CancelledError:
            if self.store.turn(turn.turn_id).status == "queued" and not (
                self._closed and self.store.is_notification(turn.turn_id)
            ):
                self._finish(turn, "cancelled", error="本轮已停止")
            raise
        except Exception:
            logger.exception("子代理轮次执行失败: %s", turn.turn_id)
            self._finish(turn, "failed", error="子代理轮次执行失败")

    async def _run(self, agent: Subagent, queued: SubagentTurn) -> None:
        turn = self.store.start(queued.turn_id)
        self._publish_latest(agent.agent_id)
        writer = TimelineWriter(self, agent.agent_id, turn.turn_id)
        executor = self._executors.get(agent.agent_id)
        try:
            if executor is None:
                executor = self.executor_factory(agent.executor)
                self._executors[agent.agent_id] = executor
            self._registry.notify_progress(
                turn.turn_id,
                {
                    "agent_id": agent.agent_id,
                    "turn_id": turn.turn_id,
                    "executor": agent.executor,
                    "phase": "running",
                },
            )
            output = await executor.run(
                agent,
                turn,
                writer.emit,
                lambda handle: self.store.save_handle(agent.agent_id, handle),
            )
            writer.flush()
            self._finish(turn, "completed", output)
        except asyncio.CancelledError:
            writer.flush()
            self._finish(turn, "cancelled", error="本轮已停止")
            if executor:
                await executor.close()
            raise
        except Exception as error:
            writer.flush()
            status = "failed"
            if isinstance(error, SubagentBlockedError):
                status = "blocked"
            elif isinstance(error, SubagentDeliveryUnknownError):
                status = "interrupted"
            self._finish(
                turn,
                status,
                error=str(error),
                questions=error.questions
                if isinstance(error, SubagentQuestionError)
                else None,
            )
            if executor:
                await executor.close()

    def _finish(
        self,
        turn: SubagentTurn,
        status: str,
        output: str = "",
        error: str = "",
        questions: list[dict] | None = None,
    ) -> None:
        if (
            status != "completed"
            and not output
            and self.store.is_notification(turn.turn_id)
        ):
            output = turn.prompt
        self.store.finish(turn.turn_id, status, output, error, questions=questions)
        self._publish_latest(turn.agent_id)
        path = self.output_dir / f"{turn.turn_id}.txt"
        try:
            path.write_text(output or error, encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            logger.exception("子代理输出文件写入失败；完整结果仍保存在会话数据库")
        registry = self._registry
        registry.update_status(
            turn.turn_id,
            TaskStatus.COMPLETED if status == "completed" else TaskStatus.FAILED,
            error=error or None,
        )
        event = self._finished.pop(turn.turn_id, None)
        if event:
            event.set()
        # A Lumi subagent can itself own subagents. Deliver their results through
        # another persisted turn instead of depending on a WebSocket connection.
        owner = self.store.agent(self.store.agent(turn.agent_id).parent_id)
        if owner and turn.notify:
            self._forward_results(owner.agent_id)

    def _publish_latest(self, agent_id: str) -> None:
        if self.on_event is None:
            return
        row = self.store.conn.execute(
            "SELECT seq FROM subagent_events WHERE agent_id=? ORDER BY seq DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        if row:
            try:
                self.on_event(
                    agent_id, self.store.history(agent_id, row["seq"] - 1, 1)[0]
                )
            except Exception:
                logger.exception("子代理观察者失败")

    async def stop(self, parent_id: str, agent_id: str) -> bool:
        self.store.owned_agent(parent_id, agent_id)
        tasks = {
            turn_id: task
            for turn_id, task in self._turn_tasks.items()
            if self.store.turn(turn_id).agent_id == agent_id
        }
        if not tasks:
            return False
        await self._cancel_turns(tasks)
        return True

    async def _cancel_turns(self, tasks: dict[str, asyncio.Task]) -> None:
        """Drain execution and done callbacks before their records or database disappear."""
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        # gather may return immediately for finished tasks, before their callbacks run.
        for turn_id in tasks:
            self._turn_done(turn_id)

    async def delete_owned(self, parent_id: str) -> list[str]:
        """Stop owners before traversing their descendants; no more fan-out can race deletion."""
        removed = []
        self._retiring.add(parent_id)
        try:
            await self._cancel_background(parent_id)
            for agent in self.store.list_agents(parent_id):
                agent_id = agent["agent_id"]
                await self.stop(parent_id, agent_id)
                removed.extend(await self.delete_owned(agent_id))
                if executor := self._executors.pop(agent_id, None):
                    await executor.close()
                for turn_id in self.store.delete_agent(agent_id):
                    (self.output_dir / f"{turn_id}.txt").unlink(missing_ok=True)
                removed.append(agent_id)
                self._agent_locks.pop(agent_id, None)
            self._registry.notification_queue.drain_for(parent_id)
            self._registry.clear_finished(parent_id)
        finally:
            self._retiring.discard(parent_id)
        return removed

    async def _cancel_background(self, owner_id: str) -> None:
        from lumi.agents.runtime.bg_process import cancel_thread_bg_tasks

        tasks = [
            entry.async_task
            for entry in self._registry.all_tasks()
            if entry.thread_id == owner_id
            and entry.status == TaskStatus.RUNNING
            and entry.async_task is not None
        ]
        await cancel_thread_bg_tasks(owner_id)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_turns(self._turn_tasks.copy())
        for agent_id in self.store.agent_ids():
            await self._cancel_background(agent_id)
        for executor in self._executors.values():
            await executor.close()
        self._registry.remove_notification_handler(self._background_complete)
        self.store.close()
        self._lock_file.close()

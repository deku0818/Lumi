"""A persistent Lumi graph with its own checkpoint, tools and shell scope."""

from __future__ import annotations

from contextlib import aclosing
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from lumi.agents.core.stream_events import tool_event_args, tool_event_call_id

from .types import (
    Emit,
    SaveHandle,
    Subagent,
    SubagentExecutor,
    SubagentQuestionError,
    SubagentTurn,
)


class LumiExecutor(SubagentExecutor):
    async def run(
        self, agent: Subagent, turn: SubagentTurn, emit: Emit, save_handle: SaveHandle
    ) -> str:
        from lumi.agents.core.graph import create_agent
        from lumi.agents.core.hooks import build_config_hooks, set_run_config_hooks
        from lumi.agents.core.meta_message import extract_text_content
        from lumi.agents.runtime.bg_tasks import current_thread_id
        from lumi.agents.runtime.shell_session import run_with_shell
        from lumi.agents.runtime.workspace import set_project_source
        from lumi.agents.tools import get_tools
        from lumi.agents.tools.providers.agent import _subagent_tools
        from lumi.agents.tools.providers.ask import ask
        from lumi.utils.config import get_config

        project = Path(agent.workspace)
        settings = get_config().config.agents
        tools = await get_tools(
            tools=agent.definition.get("tools") or None, project_dir=project
        )
        if not any(tool.name == "ask" for tool in tools):
            tools = [*tools, ask]
        lumi, context = await create_agent(
            tools=_subagent_tools(tools, agent.depth, settings.max_delegation_depth),
            system_prompt=agent.definition.get("system_prompt") or None,
            model_name=agent.definition.get("model") or None,
            checkpoint=settings.checkpoint,
            project_dir=project,
            enable_memory=False,
        )
        context.env_extra = agent.env_extra
        context.tool_mode = agent.tool_mode
        questions: list[dict] = []
        context.request_parent_input = questions.extend
        context.env_extra += (
            "\n需要补充信息时调用 ask，问题将交给主 Agent；不要编造用户的选择。"
        )
        config = {
            "configurable": {"thread_id": agent.agent_id},
            "metadata": {"workspace_dir": agent.workspace, "subagent": True},
            "recursion_limit": settings.recursion_limit,
        }
        current_thread_id.set(agent.agent_id)
        set_project_source(lambda: project)
        set_run_config_hooks(build_config_hooks(project))
        save_handle({"thread_id": agent.agent_id})

        async def execute() -> str:
            from lumi.agents.core.node_helpers.messages import dangling_tool_calls
            from lumi.agents.core.nodes import build_reject_messages

            snapshot = await lumi.graph.aget_state(config)
            dangling = dangling_tool_calls((snapshot.values or {}).get("messages", []))
            inputs = {
                "messages": [
                    *build_reject_messages(dangling, content="上一轮已停止"),
                    HumanMessage(
                        content=turn.prompt,
                        id=turn.turn_id + ":input",
                        additional_kwargs={
                            "lumi": {
                                "source": "parent_agent",
                                "parent_thread_id": agent.parent_id,
                            }
                        },
                    ),
                ],
                "depth": agent.depth,
                "tool_cancelled": False,
                "ptl_retry": False,
            }
            async with aclosing(
                lumi.graph.astream_events(
                    inputs, config=config, context=context, version="v2"
                )
            ) as events:
                async for event in events:
                    translate_lumi_event(event, emit)
            final = await lumi.graph.aget_state(config)
            if questions:
                raise SubagentQuestionError(questions)
            if final.next:
                raise RuntimeError("子代理在完成前被中断")
            messages = final.values.get("messages") or []
            return extract_text_content(messages[-1].content) if messages else ""

        try:
            return await run_with_shell(agent.agent_id, execute())
        finally:
            await lumi.aclose()

    async def close(self) -> None:
        pass


def translate_lumi_event(event: dict[str, Any], emit: Emit) -> None:
    """Keep native tool identities and hide compaction/model-internal calls."""
    from lumi.agents.core.meta_message import (
        extract_text_content,
        extract_thinking_content,
    )

    kind = event.get("event", "")
    node = event.get("metadata", {}).get("langgraph_node")
    data = event.get("data", {})
    provenance = {
        "run_id": event.get("run_id", ""),
        "parent_run_ids": event.get("parent_ids", []),
    }
    if kind.startswith("on_chat_model"):
        if node != "CallModel":
            if node == "Compact" and kind != "on_chat_model_stream":
                emit("compaction.status", {"active": kind == "on_chat_model_start"})
            return
        if kind == "on_chat_model_start":
            emit("message.start", provenance)
        elif kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk is None:
                return
            if text := extract_text_content(chunk.content):
                emit("message.delta", {"text": text, **provenance})
            if thinking := extract_thinking_content(chunk):
                emit("thinking.delta", {"text": thinking, **provenance})
        elif kind == "on_chat_model_end":
            output = data.get("output")
            emit(
                "message.complete",
                {
                    "message_id": getattr(output, "id", ""),
                    "text": extract_text_content(getattr(output, "content", "")),
                    "usage": getattr(output, "usage_metadata", None),
                    **provenance,
                },
            )
    elif kind in ("on_tool_start", "on_tool_end", "on_tool_error"):
        payload = {
            "name": event.get("name", ""),
            "tool_call_id": tool_event_call_id(event),
            **provenance,
        }
        if kind == "on_tool_start":
            args = tool_event_args(event)
            args.pop("config", None)  # The persisted timeline also omits run config.
            payload["args"] = args
            emit("tool.start", payload)
        else:
            output = data.get("output", data.get("error", ""))
            payload["output"] = extract_text_content(
                getattr(output, "content", str(output))
            )
            payload["is_error"] = (
                kind == "on_tool_error" or getattr(output, "status", "") == "error"
            )
            emit("tool.complete", payload)

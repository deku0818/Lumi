"""TUI <-> LumiAgent 桥接层

直接调用 LumiAgent graph（不走 HTTP），镜像 lumi/api/app.py 的初始化模式。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, AsyncGenerator

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

from lumi.agents.core.graph import LumiAgent, create_agent
from lumi.agents.core.node import APPROVAL_BYPASS_TOOLS
from lumi.agents.core.scheme import LumiAgentContext
from lumi.agents.tools.providers.mcp import get_mcp_session_manager
from lumi.agents.tools.session import get_session_manager
from lumi.utils.logger import logger
from lumi.utils.model_manager import get_default_model_name
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# LangChain 框架注入的内部字段，不传递给 TUI 渲染
_TOOL_INTERNAL_KEYS = frozenset({"tool_call_id", "runtime"})


class EventKind(StrEnum):
    """Bridge 事件类型"""

    MODEL_START = "model_start"
    STREAM_TOKEN = "stream_token"
    MODEL_END = "model_end"
    TOOL_CALL_CHUNK = "tool_call_chunk"  # LLM 正在生成工具调用参数
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    ASK = "ask"
    TOOL_APPROVAL = "tool_approval"
    DONE = "done"
    ERROR = "error"


@dataclass
class BridgeEvent:
    """Bridge 事件数据"""

    kind: EventKind
    text: str = ""
    name: str = ""
    args: dict | None = None
    tool_call_id: str = ""
    output: str = ""
    data: dict | None = None
    error: str = ""
    approval_mode: bool = False  # 是否处于审批模式（工具需要用户审批确认）
    usage_metadata: dict | None = None  # token 用量信息
    parent_run_id: str = ""  # 非空时表示该事件属于某个 agent 工具的子代理
    run_id: str = ""  # agent 工具自身的 run_id，用于并发 agent 场景下的精确映射


class AgentBridge:
    """TUI 与 LumiAgent 的桥接层"""

    def __init__(self) -> None:
        self._agent: LumiAgent | None = None
        self._context: LumiAgentContext | None = None
        self._config: RunnableConfig | None = None
        self.model_name: str = ""
        # 活跃的 agent 工具 run_id 集合，跨 _stream 调用保持追踪（审批恢复场景）
        self._active_agent_runs: set[str] = set()

    async def initialize(self) -> None:
        """初始化 Agent"""
        agents_config = get_config().config.agents
        self._agent, self._context = await create_agent(
            checkpoint=agents_config.checkpoint,
        )
        self.model_name = get_default_model_name()
        thread_id = generate_thread_id()
        recursion_limit = agents_config.recursion_limit
        self._config = RunnableConfig(
            configurable={"thread_id": thread_id},
            recursion_limit=recursion_limit,
        )
        logger.info(
            f"[AgentBridge] 初始化完成, model={self.model_name}, thread={thread_id}"
        )

    @property
    def current_thread_id(self) -> str:
        """当前会话的 thread_id"""
        if self._config is None:
            return ""
        return self._config.get("configurable", {}).get("thread_id", "")

    @property
    def graph(self) -> "CompiledStateGraph | None":
        """底层 LangGraph 编译图实例，用于 get_state 等操作"""
        return self._agent.graph if self._agent else None

    def switch_thread(self, thread_id: str) -> None:
        """切换到指定的会话线程

        Args:
            thread_id: 目标会话的 thread_id
        """
        recursion_limit = get_config().config.agents.recursion_limit
        self._config = RunnableConfig(
            configurable={"thread_id": thread_id},
            recursion_limit=recursion_limit,
        )
        logger.info("[AgentBridge] 切换到会话: %s", thread_id)

    async def stream_response(
        self, content: str | list, tool_mode: str = "auto"
    ) -> AsyncGenerator[BridgeEvent, None]:
        """发送消息并 yield 事件流

        Args:
            content: 纯文本字符串或多模态 content blocks 列表。
            tool_mode: 工具执行模式。
        """
        # 新一轮对话，清理上一轮残留的 agent 追踪状态
        self._active_agent_runs.clear()
        input_data = {
            "messages": [HumanMessage(content=content)],
            "tool_mode": tool_mode,
        }
        async for event in self._stream(input_data, tool_mode=tool_mode):
            yield event

    async def stream_resume(self, value) -> AsyncGenerator[BridgeEvent, None]:
        """恢复中断并 yield 事件流

        恢复时工具已通过审批，使用 "auto" 跳过审批判断。
        清理 _active_agent_runs：replay 会重新发出 agent 的 start 事件来重建。
        """
        self._active_agent_runs.clear()
        input_data = Command(resume=value)
        async for event in self._stream(input_data, tool_mode="auto"):
            yield event

    def drain_notifications(self) -> list[str]:
        """从 BackgroundTaskManager 的 NotificationQueue 中取出所有待发送通知"""
        sm = get_session_manager()
        if not sm.has_bg_manager:
            return []
        return sm.bg_manager.notification_queue.drain_all()

    async def close(self) -> None:
        """清理资源"""
        if self._agent is not None:
            await self._agent.aclose()
        await get_mcp_session_manager().close()
        await get_session_manager().close_all()

    async def _stream(
        self, input_data, *, tool_mode: str = "auto"
    ) -> AsyncGenerator[BridgeEvent, None]:
        """核心流式处理 - yield BridgeEvent

        Args:
            input_data: 输入数据（消息或 Command）
            tool_mode: 工具执行模式，用于判断审批状态
        """
        try:
            if self._agent is None or self._config is None:
                yield BridgeEvent(
                    kind=EventKind.ERROR, error="Agent 未初始化，请重启 Lumi"
                )
                return
            graph = self._agent.graph
            async for event in graph.astream_events(
                input_data,
                self._config,
                context=self._context,
            ):
                kind = event.get("event", "")
                run_id = event.get("run_id", "")
                parent_ids = event.get("parent_ids", [])

                # 判断当前事件是否属于子代理：
                # 排除当前事件自身的 run_id，避免 agent 工具的
                # on_tool_start 把自己误判为子代理事件
                parent_id = ""
                if self._active_agent_runs and parent_ids:
                    candidates = self._active_agent_runs & (set(parent_ids) - {run_id})
                    if candidates:
                        parent_id = next(iter(candidates))

                # agent 工具开始时记录 run_id（放在匹配之后，
                # 确保 agent 自身的 on_tool_start 不会自匹配）
                if kind == "on_tool_start" and event.get("name") == "agent":
                    self._active_agent_runs.add(run_id)

                # agent 工具结束时移除 run_id，避免残留影响后续匹配
                if kind == "on_tool_end" and event.get("name") == "agent":
                    self._active_agent_runs.discard(run_id)

                if kind == "on_chat_model_start":
                    yield BridgeEvent(
                        kind=EventKind.MODEL_START,
                        parent_run_id=parent_id,
                    )

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk:
                        usage = self._extract_usage(chunk)
                        text = self._extract_text_from_chunk(chunk)
                        if text:
                            yield BridgeEvent(
                                kind=EventKind.STREAM_TOKEN,
                                text=text,
                                usage_metadata=usage,
                                parent_run_id=parent_id,
                            )
                        elif self._has_tool_call_chunk(chunk):
                            yield BridgeEvent(
                                kind=EventKind.TOOL_CALL_CHUNK,
                                usage_metadata=usage,
                                parent_run_id=parent_id,
                            )

                elif kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    usage = self._extract_usage(output) if output else None
                    yield BridgeEvent(
                        kind=EventKind.MODEL_END,
                        usage_metadata=usage,
                        parent_run_id=parent_id,
                    )

                elif kind == "on_tool_start":
                    name = event.get("name", "unknown")
                    data = event.get("data", {})
                    args = data.get("input", {})
                    if isinstance(args, dict):
                        tool_call_id = args.get("tool_call_id", "")
                        args = {
                            k: v
                            for k, v in args.items()
                            if k not in _TOOL_INTERNAL_KEYS
                        }
                    else:
                        tool_call_id = ""
                        args = {}
                    approval_mode = (
                        tool_mode != "auto" and name not in APPROVAL_BYPASS_TOOLS
                    )
                    yield BridgeEvent(
                        kind=EventKind.TOOL_START,
                        name=name,
                        args=args,
                        tool_call_id=tool_call_id,
                        approval_mode=approval_mode,
                        parent_run_id=parent_id,
                        run_id=run_id if name == "agent" else "",
                    )

                elif kind == "on_tool_end":
                    name = event.get("name", "unknown")
                    data = event.get("data", {})
                    output = data.get("output", "")
                    if isinstance(output, Command):
                        msgs = (output.update or {}).get("messages", [])
                        if msgs and hasattr(msgs[0], "content"):
                            output = msgs[0].content
                        else:
                            output = ""
                    elif hasattr(output, "content"):
                        output = output.content
                    tool_call_id = ""
                    inp = data.get("input", {})
                    if isinstance(inp, dict):
                        tool_call_id = inp.get("tool_call_id", "")
                    yield BridgeEvent(
                        kind=EventKind.TOOL_END,
                        name=name,
                        output=str(output) if output else "",
                        tool_call_id=tool_call_id,
                        parent_run_id=parent_id,
                        run_id=run_id if name == "agent" else "",
                    )

            # 流结束后检测中断
            yield await self._check_interrupts()

        except asyncio.CancelledError:
            logger.info("[AgentBridge] 流式任务被取消")
            raise
        except Exception as e:
            logger.error(f"[AgentBridge] 流式事件错误: {e}", exc_info=True)
            yield BridgeEvent(kind=EventKind.ERROR, error=str(e))

    def _subagent_marker(self) -> str:
        """如果当前有活跃的 agent 工具运行，返回其 run_id 作为子代理标记。"""
        if self._active_agent_runs:
            return next(iter(self._active_agent_runs))
        return ""

    async def _check_interrupts(self) -> BridgeEvent:
        """检查中断，返回对应事件"""
        try:
            graph = self._agent.graph
            state = await graph.aget_state(self._config)
        except Exception as e:
            thread_id = (
                (self._config or {}).get("configurable", {}).get("thread_id", "unknown")
            )
            logger.error(
                "[AgentBridge] 获取中断状态失败: %s (thread_id=%s)",
                e,
                thread_id,
                exc_info=True,
            )
            return BridgeEvent(kind=EventKind.DONE)

        if not state.next:
            # 从 state 的最后一条 AI message 提取完整 usage（含 cache 详情）
            usage = self._extract_last_ai_usage(state)
            return BridgeEvent(kind=EventKind.DONE, usage_metadata=usage)

        for task in state.tasks:
            for intr in task.interrupts:
                data = intr.value
                if isinstance(data, dict):
                    interrupt_type = data.get("type", "")
                    if interrupt_type == "ask":
                        return BridgeEvent(
                            kind=EventKind.ASK,
                            data=data,
                            parent_run_id=self._subagent_marker(),
                        )
                    elif interrupt_type == "tool_approval":
                        return BridgeEvent(
                            kind=EventKind.TOOL_APPROVAL,
                            data=data,
                            parent_run_id=self._subagent_marker(),
                        )

        logger.warning(f"[AgentBridge] 未知中断类型, next={state.next}")
        return BridgeEvent(kind=EventKind.DONE)

    @classmethod
    def _extract_last_ai_usage(cls, state) -> dict | None:
        """从 graph state 的最后一条 AI message 提取 usage_metadata。

        作为 on_chat_model_end 的补充数据源，某些 API 在 state 中保留了
        比流式聚合更完整的 usage（如 cache 详情）。
        复用 _extract_usage 确保 input_token_details 等字段被一致提取。
        """
        messages = (state.values or {}).get("messages", [])
        for msg in reversed(messages):
            if getattr(msg, "usage_metadata", None) is not None:
                return cls._extract_usage(msg)
        return None

    @staticmethod
    def _extract_text_from_chunk(chunk) -> str:
        """从 LangChain chunk 中提取文本"""
        if hasattr(chunk, "content"):
            content = chunk.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text", "")
        return ""

    @staticmethod
    def _extract_usage(obj) -> dict | None:
        """从 LangChain 对象中提取 usage_metadata。"""
        um = getattr(obj, "usage_metadata", None)
        if um is None:
            return None
        if isinstance(um, dict):
            return um if um else None
        # UsageMetadata (TypedDict subclass) → 转 dict，保留 input_token_details
        result = {
            "input_tokens": getattr(um, "input_tokens", 0),
            "output_tokens": getattr(um, "output_tokens", 0),
            "total_tokens": getattr(um, "total_tokens", 0),
        }
        itd = getattr(um, "input_token_details", None)
        if itd:
            result["input_token_details"] = (
                dict(itd) if not isinstance(itd, dict) else itd
            )
        otd = getattr(um, "output_token_details", None)
        if otd:
            result["output_token_details"] = (
                dict(otd) if not isinstance(otd, dict) else otd
            )
        return result

    @staticmethod
    def _has_tool_call_chunk(chunk) -> bool:
        """检测 chunk 是否包含工具调用数据"""
        # LangChain AIMessageChunk 的 tool_call_chunks 属性
        if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
            return True
        # 备用：检查 additional_kwargs 中的 tool_calls
        if hasattr(chunk, "additional_kwargs"):
            if chunk.additional_kwargs.get("tool_calls"):
                return True
        return False

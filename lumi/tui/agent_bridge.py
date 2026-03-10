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
from lumi.agents.tools.session import get_session_manager
from lumi.utils.logger import logger
from lumi.utils.model_manager import get_default_model_name
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


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


class AgentBridge:
    """TUI 与 LumiAgent 的桥接层"""

    def __init__(self) -> None:
        self._agent: LumiAgent | None = None
        self._context: LumiAgentContext | None = None
        self._config: RunnableConfig | None = None
        self.model_name: str = ""

    async def initialize(self) -> None:
        """初始化 Agent"""
        self._agent, self._context = await create_agent(
            checkpoint=get_config().config.agents.checkpoint,
        )
        self.model_name = get_default_model_name()
        thread_id = generate_thread_id()
        self._config = RunnableConfig(configurable={"thread_id": thread_id})
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
        self._config = RunnableConfig(configurable={"thread_id": thread_id})
        logger.info("[AgentBridge] 切换到会话: %s", thread_id)

    async def stream_response(
        self, content: str | list, tool_mode: str = "auto"
    ) -> AsyncGenerator[BridgeEvent, None]:
        """发送消息并 yield 事件流

        Args:
            content: 纯文本字符串或多模态 content blocks 列表。
            tool_mode: 工具执行模式。
        """
        input_data = {
            "messages": [HumanMessage(content=content)],
            "tool_mode": tool_mode,
        }
        async for event in self._stream(input_data, tool_mode=tool_mode):
            yield event

    async def stream_resume(self, value) -> AsyncGenerator[BridgeEvent, None]:
        """恢复中断并 yield 事件流

        恢复时工具已通过审批，使用 "auto" 跳过审批判断。
        """
        input_data = Command(resume=value)
        async for event in self._stream(input_data, tool_mode="auto"):
            yield event

    async def close(self) -> None:
        """清理资源"""
        if self._agent is not None:
            await self._agent.aclose()
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

                if kind == "on_chat_model_start":
                    yield BridgeEvent(kind=EventKind.MODEL_START)

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk:
                        text = self._extract_text_from_chunk(chunk)
                        if text:
                            yield BridgeEvent(kind=EventKind.STREAM_TOKEN, text=text)
                        elif self._has_tool_call_chunk(chunk):
                            # LLM 正在生成工具调用参数，通知 TUI 显示 loading
                            yield BridgeEvent(kind=EventKind.TOOL_CALL_CHUNK)

                elif kind == "on_chat_model_end":
                    yield BridgeEvent(kind=EventKind.MODEL_END)

                elif kind == "on_tool_start":
                    name = event.get("name", "unknown")
                    data = event.get("data", {})
                    args = data.get("input", {})
                    if isinstance(args, dict):
                        tool_call_id = args.get("tool_call_id", "")
                        args = {k: v for k, v in args.items() if k != "tool_call_id"}
                    else:
                        tool_call_id = ""
                        args = {}
                    # 判断是否处于审批模式：非 auto 模式且工具不在免审批列表中
                    approval_mode = (
                        tool_mode != "auto" and name not in APPROVAL_BYPASS_TOOLS
                    )
                    yield BridgeEvent(
                        kind=EventKind.TOOL_START,
                        name=name,
                        args=args,
                        tool_call_id=tool_call_id,
                        approval_mode=approval_mode,
                    )

                elif kind == "on_tool_end":
                    name = event.get("name", "unknown")
                    data = event.get("data", {})
                    output = data.get("output", "")
                    # Command 返回值（如 ask 工具）不适合直接 str()，
                    # 提取其中 ToolMessage 的 content 作为展示文本
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
                    )

            # 流结束后检测中断
            yield await self._check_interrupts()

        except asyncio.CancelledError:
            logger.info("[AgentBridge] 流式任务被取消")
            raise
        except Exception as e:
            logger.error(f"[AgentBridge] 流式事件错误: {e}", exc_info=True)
            yield BridgeEvent(kind=EventKind.ERROR, error=str(e))

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
            return BridgeEvent(kind=EventKind.DONE)

        for task in state.tasks:
            for intr in task.interrupts:
                data = intr.value
                if isinstance(data, dict):
                    interrupt_type = data.get("type", "")
                    if interrupt_type == "ask":
                        return BridgeEvent(kind=EventKind.ASK, data=data)
                    elif interrupt_type == "tool_approval":
                        return BridgeEvent(kind=EventKind.TOOL_APPROVAL, data=data)

        logger.warning(f"[AgentBridge] 未知中断类型, next={state.next}")
        return BridgeEvent(kind=EventKind.DONE)

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

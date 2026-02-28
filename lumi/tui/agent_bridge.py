"""TUI <-> LumiAgent 桥接层

直接调用 LumiAgent graph（不走 HTTP），镜像 lumi/api/app.py 的初始化模式。
"""

import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from lumi.agents.core.graph import LumiAgent
from lumi.agents.core.scheme import LumiAgentContext
from lumi.agents.tools import get_tools
from lumi.agents.tools.session import get_session_manager
from lumi.utils.logger import logger
from lumi.utils.model_manager import DEFAULT_MODEL_NAME
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id


# 事件数据类型
@dataclass
class BridgeEvent:
    kind: str  # "model_start" | "stream_token" | "model_end" | "tool_start" | "tool_end" | "ask" | "tool_approval" | "done" | "error"
    text: str = ""
    name: str = ""
    args: dict | None = None
    tool_call_id: str = ""
    output: str = ""
    data: dict | None = None
    error: str = ""


class AgentBridge:
    """TUI 与 LumiAgent 的桥接层"""

    def __init__(self) -> None:
        self._agent: LumiAgent | None = None
        self._context: LumiAgentContext | None = None
        self._config: RunnableConfig | None = None
        self._cancel_event: asyncio.Event | None = None
        self.model_name: str = DEFAULT_MODEL_NAME

    async def initialize(self) -> None:
        """初始化 Agent"""
        tools = await get_tools()
        checkpointer = MemorySaver()
        self._agent = LumiAgent(checkpointer=checkpointer)
        self._context = LumiAgentContext(
            tools=tools,
            system_prompt=get_config().load_system_prompt(),
            model_name=DEFAULT_MODEL_NAME,
        )
        thread_id = generate_thread_id()
        self._config = RunnableConfig(configurable={"thread_id": thread_id})
        logger.info(
            f"[AgentBridge] 初始化完成, model={DEFAULT_MODEL_NAME}, thread={thread_id}"
        )

    async def stream_response(
        self, text: str, tool_mode: str = "approve"
    ) -> AsyncGenerator[BridgeEvent, None]:
        """发送消息并 yield 事件流"""
        input_data = {
            "messages": [HumanMessage(content=text)],
            "tool_mode": tool_mode,
        }
        async for event in self._stream(input_data):
            yield event

    async def stream_resume(self, value) -> AsyncGenerator[BridgeEvent, None]:
        """恢复中断并 yield 事件流"""
        input_data = Command(resume=value)
        async for event in self._stream(input_data):
            yield event

    async def close(self) -> None:
        """清理资源"""
        await get_session_manager().close_all()

    async def _stream(self, input_data) -> AsyncGenerator[BridgeEvent, None]:
        """核心流式处理 - yield BridgeEvent"""
        graph = self._agent.graph

        try:
            async for event in graph.astream_events(
                input_data,
                self._config,
                context=self._context,
            ):
                kind = event.get("event", "")

                if kind == "on_chat_model_start":
                    yield BridgeEvent(kind="model_start")

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk:
                        text = self._extract_text_from_chunk(chunk)
                        if text:
                            yield BridgeEvent(kind="stream_token", text=text)

                elif kind == "on_chat_model_end":
                    yield BridgeEvent(kind="model_end")

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
                    yield BridgeEvent(
                        kind="tool_start",
                        name=name,
                        args=args,
                        tool_call_id=tool_call_id,
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
                        kind="tool_end",
                        name=name,
                        output=str(output) if output else "",
                        tool_call_id=tool_call_id,
                    )

            # 流结束后检测中断
            interrupt_event = await self._check_interrupts()
            yield interrupt_event

        except asyncio.CancelledError:
            logger.info("[AgentBridge] 流式任务被取消")
            raise
        except Exception as e:
            logger.error(f"[AgentBridge] 流式事件错误: {e}", exc_info=True)
            yield BridgeEvent(kind="error", error=str(e))

    async def _check_interrupts(self) -> BridgeEvent:
        """检查中断，返回对应事件"""
        graph = self._agent.graph
        state = await graph.aget_state(self._config)

        if not state.next:
            return BridgeEvent(kind="done")

        for task in state.tasks:
            for intr in task.interrupts:
                data = intr.value
                if isinstance(data, dict):
                    interrupt_type = data.get("type", "")
                    if interrupt_type == "ask":
                        return BridgeEvent(kind="ask", data=data)
                    elif interrupt_type == "tool_approval":
                        return BridgeEvent(kind="tool_approval", data=data)

        logger.warning(f"[AgentBridge] 未知中断类型, next={state.next}")
        return BridgeEvent(kind="done")

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

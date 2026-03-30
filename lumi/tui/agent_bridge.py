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
from lumi.agents.tools.checkpoint import CheckpointInfo, FileCheckpointManager
from lumi.agents.tools.file_tracker import FileChangeTracker
from lumi.agents.tools.providers.mcp import get_mcp_session_manager
from lumi.agents.tools.session import get_session_manager
from lumi.utils.logger import logger
from lumi.utils.model_manager import get_default_model_name
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id
from lumi.utils.workspace_id import get_workspace_dir

if TYPE_CHECKING:
    from pathlib import Path
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
    EXIT_PLAN_MODE = "ExitPlanMode"
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
        self._shadow: FileCheckpointManager | None = None
        self._tracker: FileChangeTracker | None = None

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
            metadata={"workspace_dir": get_workspace_dir()},
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
            metadata={"workspace_dir": get_workspace_dir()},
            recursion_limit=recursion_limit,
        )
        # 切换 checkpoint manager（复用 tracker）
        if self._shadow is not None and self._tracker is not None:
            self._shadow = FileCheckpointManager(
                thread_id, self._shadow.project_dir, self._tracker
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
        # 在 agent 执行前创建 checkpoint（快照当前文件状态）
        await self._create_checkpoint_before_turn(content)

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
        保留 _active_agent_runs：子代理内部工具审批后 resume 可能不会
        重新发出 agent 的 on_tool_start，需要保留已有映射才能正确识别
        后续子代理事件的 parent_run_id。
        """
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

            # 检测残留图状态（待执行节点但无中断），自动恢复
            await self._recover_stale_state(graph)

            try:
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
                        candidates = self._active_agent_runs & (
                            set(parent_ids) - {run_id}
                        )
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
                        yield BridgeEvent(
                            kind=EventKind.TOOL_START,
                            name=name,
                            args=args,
                            tool_call_id=tool_call_id,
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

                        # ask 等 BYPASS 工具使用 interrupt() 中断，LangGraph 会在
                        # 中断时提前发出 on_tool_end（output 为空），此时不应标记
                        # ToolBlock 为 Done，否则后续 ASK 事件找不到 block 来挂载对话框。
                        # 真正的 TOOL_END 在 resume 后才会带有实际 output。
                        resolved_output = str(output) if output else ""
                        if name in APPROVAL_BYPASS_TOOLS and not resolved_output:
                            continue

                        yield BridgeEvent(
                            kind=EventKind.TOOL_END,
                            name=name,
                            output=resolved_output,
                            tool_call_id=tool_call_id,
                            parent_run_id=parent_id,
                            run_id=run_id if name == "agent" else "",
                        )

            finally:
                # 清除 rewind 或恢复时设置的 checkpoint_id，
                # 确保后续 aget_state 获取最新 checkpoint
                self._config["configurable"].pop("checkpoint_id", None)

            # 流结束后检测中断
            yield await self._check_interrupts()

        except asyncio.CancelledError:
            logger.info("[AgentBridge] 流式任务被取消")
            raise
        except Exception as e:
            err_type = type(e).__qualname__
            err_module = type(e).__module__ or ""
            cause = e.__cause__
            cause_info = f", cause={type(cause).__qualname__}: {cause}" if cause else ""
            logger.error(
                "[AgentBridge] 流式事件错误: [%s.%s] %s%s",
                err_module,
                err_type,
                e,
                cause_info,
                exc_info=True,
            )
            yield BridgeEvent(kind=EventKind.ERROR, error=f"[{err_type}] {e}")

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
                    elif interrupt_type == "ExitPlanMode":
                        return BridgeEvent(
                            kind=EventKind.EXIT_PLAN_MODE,
                            data=data,
                            parent_run_id=self._subagent_marker(),
                        )

        # 记录详细的中断信息以便排查
        tasks_info = []
        for task in state.tasks:
            interrupts_info = [
                {"type": type(intr.value).__name__, "value": repr(intr.value)[:200]}
                for intr in (task.interrupts or [])
            ]
            tasks_info.append(
                {"id": task.id, "name": task.name, "interrupts": interrupts_info}
            )
        logger.error(
            "[AgentBridge] 图状态异常: next=%s 但无可识别的中断, tasks=%s",
            state.next,
            tasks_info,
        )
        return BridgeEvent(
            kind=EventKind.ERROR,
            error=f"执行异常：图停滞在 {state.next}，可能是上一轮请求失败导致状态残留，请重试",
        )

    async def _recover_stale_state(self, graph: "CompiledStateGraph") -> None:
        """检测并恢复残留的图状态。

        当上一轮执行异常或 rewind 后，checkpoint 可能残留待执行节点但无中断，
        导致 astream_events 无法正常启动新的执行。此方法回退到最近的干净
        checkpoint（state.next 为空），使下次 astream_events 能正常工作。
        """
        try:
            state = await graph.aget_state(self._config)
        except Exception:
            logger.warning(
                "[AgentBridge] 无法获取图状态进行残留检测，跳过恢复",
                exc_info=True,
            )
            return

        if not state.next:
            return

        has_interrupts = any(intr for task in state.tasks for intr in task.interrupts)
        if has_interrupts:
            return

        logger.warning(
            "[AgentBridge] 检测到残留图状态 next=%s，尝试恢复",
            state.next,
        )
        clean_cp_id = await self._find_clean_checkpoint_id(graph, state)
        if clean_cp_id:
            self._config["configurable"]["checkpoint_id"] = clean_cp_id
            logger.info("[AgentBridge] 已回退到干净的 checkpoint")
        else:
            # 找不到干净的父 checkpoint，移除 checkpoint_id 让 LangGraph 重新开始
            self._config["configurable"].pop("checkpoint_id", None)
            logger.warning("[AgentBridge] 未找到干净 checkpoint，将从头开始")

    async def _find_clean_checkpoint_id(
        self, graph: "CompiledStateGraph", state
    ) -> str | None:
        """沿 parent_config 链回溯，找到 state.next 为空的 checkpoint_id。"""
        _MAX_WALK = 10
        current = state
        for _ in range(_MAX_WALK):
            parent_config = current.parent_config
            if not parent_config or "configurable" not in parent_config:
                return None
            current = await graph.aget_state(parent_config)
            if not current.next:
                return parent_config["configurable"].get("checkpoint_id")
        return None

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

    # ── File-level Checkpoint ──

    def init_checkpoint(self, project_dir: "Path") -> None:
        """初始化文件级 checkpoint manager

        Args:
            project_dir: 项目根目录路径
        """
        from pathlib import Path as _Path

        from lumi.agents.tools.providers.filesystem import _get_backend

        tid = self.current_thread_id
        if tid:
            self._tracker = FileChangeTracker()
            self._shadow = FileCheckpointManager(tid, _Path(project_dir), self._tracker)
            # 将 tracker 注册到 filesystem backend
            _get_backend().set_tracker(self._tracker)

    async def _create_checkpoint_before_turn(self, content: str | list) -> None:
        """在每轮 agent 执行前创建 checkpoint。

        从 content 提取用户消息摘要作为 label，
        从 LangGraph state 获取当前 checkpoint_id。
        """
        if self._shadow is None:
            return

        try:
            label = self._extract_label(content)

            # 获取当前 LangGraph checkpoint_id
            lg_cp_id = ""
            lg_parent_cp_id = ""
            if self._agent and self._config:
                try:
                    state = await self._agent.graph.aget_state(self._config)
                    if state and state.config:
                        configurable = state.config.get("configurable", {})
                        lg_cp_id = configurable.get("checkpoint_id", "")
                        # parent 是上一个 checkpoint
                        parent_config = state.parent_config
                        if parent_config:
                            lg_parent_cp_id = parent_config.get("configurable", {}).get(
                                "checkpoint_id", ""
                            )
                except Exception:
                    logger.warning(
                        "[AgentBridge] 获取 LangGraph checkpoint_id 失败，"
                        "checkpoint 将无法回退 LangGraph 会话",
                        exc_info=True,
                    )

            # 在线程池中执行文件操作，避免阻塞事件循环
            await asyncio.to_thread(
                self._shadow.create_checkpoint,
                label,
                lg_cp_id,
                lg_parent_cp_id,
            )
        except Exception:
            logger.error("[AgentBridge] 创建 checkpoint 失败", exc_info=True)

    async def list_checkpoints(self) -> list[CheckpointInfo]:
        """列出当前 thread 的所有 checkpoint"""
        if self._shadow is None:
            return []
        return await asyncio.to_thread(self._shadow.list_checkpoints)

    async def rewind_to_checkpoint(
        self, checkpoint: CheckpointInfo
    ) -> tuple[bool, str]:
        """回退到指定 checkpoint：恢复文件 + 回退 LangGraph 会话。

        Args:
            checkpoint: 要回退到的 checkpoint

        Returns:
            (success, error_message) 元组
        """
        if self._shadow is None:
            return False, "Checkpoint 未初始化"

        try:
            # 1. 恢复文件（在线程池中执行）
            file_ok = await asyncio.to_thread(
                self._shadow.restore_checkpoint, checkpoint.commit_hash
            )
            if not file_ok:
                return False, "文件恢复失败"

            # 2. 回退 LangGraph 会话
            # 直接将 config 指向目标 checkpoint（它是图完成时捕获的，next 为空）。
            # 下次 astream_events 调用会自然从该 checkpoint 分支出新的执行。
            # 避免使用 aupdate_state(as_node="__start__") 创建 fork，
            # 因为那会产生 next=('PreprocessMessages',) 的悬挂状态。
            if self._config and checkpoint.langgraph_checkpoint_id:
                self._config["configurable"]["checkpoint_id"] = (
                    checkpoint.langgraph_checkpoint_id
                )

            return True, ""

        except Exception as e:
            logger.error("[AgentBridge] rewind 失败", exc_info=True)
            return False, str(e)

    @staticmethod
    def _extract_label(content: str | list) -> str:
        """从用户消息中提取摘要 label"""
        if isinstance(content, str):
            return content.replace("\n", " ").strip()[:100]
        if isinstance(content, list):
            command_label = ""
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    # 从 <command-name> 标签中提取命令名作为备选 label
                    if text.startswith("<command-name>"):
                        import re

                        m = re.search(r"<command-name>(.*?)</command-name>", text)
                        if m and not command_label:
                            command_label = m.group(1).strip()
                        continue
                    # 跳过其他系统注入的 XML 块
                    if text.startswith("<"):
                        continue
                    return text.replace("\n", " ").strip()[:100]
            # 所有 text block 都是系统注入的，使用命令名作为 label
            if command_label:
                return command_label[:100]
        return "checkpoint"

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

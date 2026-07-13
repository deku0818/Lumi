"""LumiAgent 桥接层（中立层，供 TUI / desktop WS 服务等前端复用）

直接调用 LumiAgent graph（不走 HTTP），将原始 LangGraph 事件封装为干净的
BridgeEvent 流，并处理子代理追踪、权限审批富化、checkpoint 回退等。

AgentBridge 保留流式 + 会话生命周期核心；Provider CRUD / 审批富化 /
checkpoint / folder 等职责拆到 service 子模块，AgentBridge 通过瘦委派对外暴露。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from lumi.agents.core.graph import LumiAgent, create_agent
from lumi.agents.core.hooks import build_config_hooks, set_run_config_hooks
from lumi.agents.core.meta_message import synthetic_human_message
from lumi.agents.core.node_helpers.messages import inject_text_into_message
from lumi.agents.core.state import LumiAgentContext
from lumi.agents.permissions.workspace import set_run_authorized_source_for
from lumi.agents.runtime.bg_tasks import (
    compose_notification_hint,
    current_thread_id,
    get_task_registry,
)
from lumi.agents.runtime.checkpoint import CheckpointInfo, FileCheckpointManager
from lumi.agents.runtime.file_tracker import FileChangeTracker
from lumi.agents.runtime.shell_session import get_shell_session_manager
from lumi.agents.tools.providers.mcp import (
    close_all_pools,
    get_pool_status,
    pool_generation,
    project_wire_key,
)
from lumi.gateway.bridge.approval import ApprovalEnricher
from lumi.gateway.bridge.broker import LUMI_APPROVAL_EVENT, ApprovalBroker
from lumi.gateway.bridge.checkpoint import CheckpointService
from lumi.gateway.bridge.folders import FolderManager
from lumi.gateway.bridge.providers import ProviderService
from lumi.sessions.message_text import extract_text_content
from lumi.utils.constants import (
    ATTACHED_FILE_TAG,
    LUMI_META_KEY,
    MAX_STREAM_RETRIES,
    RETRY_BASE_WAIT,
)
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config
from lumi.utils.thread_id import generate_thread_id, is_channel_thread
from lumi.utils.workspace_id import get_workspace_dir

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# LangChain 框架注入的内部字段，不传递给 TUI 渲染
_TOOL_INTERNAL_KEYS = frozenset({"tool_call_id", "runtime"})


def build_skill_command_blocks(
    skill_name: str, content: str, extra_text: str = ""
) -> list[dict]:
    """构建技能斜杠命令发给 Agent 的结构化 content blocks（TUI / desktop 共用）。

    与 Agent 侧的命令解析约定保持一致，是该格式的单一事实来源：
        Block 0: <command-name>/xxx</command-name><command-type>skill</command-type>
        Block 1: <skill-content>{content}</skill-content>
        Block 2 (可选): <user-input>{extra_text}</user-input>

    Args:
        skill_name: 技能名称（不含前导 "/"）。
        content: 技能正文（通常为 skill.prompt，可能已拼接 extra_text）。
        extra_text: 用户在斜杠命令后追加的原始文本。
    """
    meta = (
        f"<command-name>/{skill_name}</command-name><command-type>skill</command-type>"
    )
    blocks: list[dict] = [
        {"type": "text", "text": meta},
        {"type": "text", "text": f"<skill-content>{content}</skill-content>"},
    ]
    if extra_text:
        blocks.append(
            {"type": "text", "text": f"<user-input>{extra_text}</user-input>"}
        )
    return blocks


def available_commands(memory_enabled: bool, *, channel: bool = False) -> list[dict]:
    """当前可用的斜杠命令（技能 + 记忆会话的 dream 系）。

    模块级函数：IM 渠道的 /help 对从未对话的 chat 也要能列命令，不必为此
    隐式构建常驻 AgentBridge；bridge.list_commands 是其绑定实例状态的薄封装。

    dream 系按载体分流（``channel`` = IM 长会话）：/dream 综合其他近期会话，只对
    desktop 短会话有意义；/dream-session 只吃当前永久会话，是 IM 的形态——两端各见其一。
    """
    from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector

    skills = SkillChangeDetector.get_instance().peek()
    commands = [
        {"name": s.name, "description": s.description, "type": "skill"} for s in skills
    ]
    if memory_enabled:
        # dream 系按载体分流：IM 长会话只见 /dream-session，desktop 短会话只见 /dream
        name, desc = (
            ("dream-session", "整理本会话记忆（后台）")
            if channel
            else ("dream", "立即整理记忆（后台综合近期会话）")
        )
        commands.append({"name": name, "description": desc, "type": "system"})
    # /compact 与记忆无关，任何会话都可手动压缩历史
    commands.append(
        {
            "name": "compact",
            "description": "压缩当前会话历史（保留摘要）",
            "type": "system",
        }
    )
    # /goal：设定会话目标，由 Stop hook 驱动直到达成（跨轮持续，达成自动解除）
    commands.append(
        {
            "name": "goal",
            "description": "设定会话目标，持续驱动直到达成（/goal clear 解除）",
            "type": "system",
        }
    )
    return commands


class EventKind(StrEnum):
    """Bridge 事件类型。

    成员值直接采用对外 wire 协议的 namespace.verb 命名（见 protocol/events.json），
    BridgeEvent.kind 即为前端收到的事件 type，无需额外映射层。
    """

    MESSAGE_START = "message.start"
    MESSAGE_DELTA = "message.delta"
    THINKING_DELTA = "thinking.delta"  # 模型思考增量（Anthropic thinking 块 / 方言 reasoning_content）
    MESSAGE_COMPLETE = "message.complete"
    TOOL_GENERATING = "tool.generating"  # LLM 正在生成工具调用参数
    COMPACTING = (
        "compaction.status"  # 历史压缩进行中（Summarizer 内部摘要调用不外泄为助手消息）
    )
    TOOL_START = "tool.start"
    TOOL_COMPLETE = "tool.complete"
    CLARIFY = "clarify.request"
    APPROVAL = "approval.request"
    TURN_COMPLETE = "turn.complete"
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
    is_error: bool = False  # TOOL_COMPLETE 专用：工具是否以异常收尾（前端红色高亮）
    usage_metadata: dict | None = None  # token 用量信息
    parent_run_id: str = ""  # 非空时表示该事件属于某个 agent 工具的子代理
    run_id: str = ""  # agent 工具自身的 run_id，用于并发 agent 场景下的精确映射


async def shutdown_shared_runtime() -> None:
    """关闭进程级共享运行时（MCP 子进程、shell / 后台任务会话）。

    进程退出时调用一次：TUI 在 quit 时、`lumi serve` 在 lifespan shutdown 时。
    """
    await close_all_pools()
    await get_shell_session_manager().close_all()


class AgentBridge:
    """TUI 与 LumiAgent 的桥接层"""

    def __init__(self) -> None:
        self._agent: LumiAgent | None = None
        self._context: LumiAgentContext | None = None
        self._config: RunnableConfig | None = None
        self.model_name: str = ""
        # 在途审批 Broker：本连接唯一实例，注入 context 供节点 / ask 工具 await 审批，
        # resolve_approval 经非流式 resume RPC 唤醒挂起的 Future（见 docs/architecture/
        # approval-inflight.md）。
        self._broker = ApprovalBroker()
        # 当前挂起的审批/澄清「已富化的对外事件」按 approval_id 留底，供 WS 重连后重发
        # （断开期会话原地挂着、Future 仍在，重连只需把卡片再推一遍）。resolve/reject 时清理。
        self._pending_approval_events: dict[str, BridgeEvent] = {}
        # 活跃 agent 工具 run_id 集合：流式 / 审批事件的子代理归属（_resolve_subagent_parent）
        # 据此判定祖先链中是否含活跃 agent run。在途审批后审批卡片也走同一归属机制。
        self._active_agent_runs: set[str] = set()
        self._shadow: FileCheckpointManager | None = None
        self._tracker: FileChangeTracker | None = None
        # 本会话临时添加的额外可访问目录（不持久化，连接断开即失效）
        self._extra_folders: list[str] = []
        # 上次通知模型时的目录快照，用于在下一条用户消息注入增减变更提醒
        self._notified_folders: set[str] = set()
        # 上次通知模型时的 ultra 档位状态，仅在开/关切换的那一轮注入边沿提醒
        self._notified_ultra: bool = False
        # 本会话项目的 config hooks（.lumi/hooks.json）：随项目绑定，set_workspace 时重载，
        # 每轮 _stream 注入 per-run contextvar。空 dict = 暂无（initialize 后填充）。
        self._config_hooks: dict = {}
        # MCP 后台加载配套：工具构建时的项目与池版本（轮首比对，换代即重建 context.tools）
        self._mcp_project: Path | None = None
        self._disabled_tools: list[str] | None = None
        self._mcp_gen: int = 0
        # 职责子模块（back-reference 组合）
        self._providers = ProviderService(self)
        self._approval = ApprovalEnricher(self)
        self._checkpoint = CheckpointService(self)
        self._folders = FolderManager(self)

    async def initialize(
        self,
        project_dir: str = "",
        disabled_tools: list[str] | None = None,
        wait_mcp: bool = False,
    ) -> None:
        """初始化 Agent。

        project_dir：本会话所属项目（open 握手经 ``?workspace=`` 携带）。给定且有效则
        引擎在创建时直接 pin 到它，无需后续 set_workspace rebase；为空 / 无效退回进程
        cwd。项目随会话绑定，不动进程级状态。
        disabled_tools：本会话禁用的工具黑名单（如飞书 channel 禁用 ``ask``）；None 时全量。
        wait_mcp：冷 MCP 池时是否等它就绪。交互会话默认 False（非阻塞 + 轮首刷新
        自愈）；headless CLI 单轮即退无自愈，传 True。
        """
        agents_config = get_config().config.agents
        target = Path(project_dir).expanduser().resolve() if project_dir else None
        if target is not None and not target.is_dir():
            logger.warning(
                "[AgentBridge] open 指定 workspace 无效，退回进程目录: %s", target
            )
            target = None
        # 无条件预算工具，把会话项目根 target 带进 MCP 分层加载（全局 ∪ 项目）；
        # 若只在 disabled_tools 非空时才 get_tools，则常见路径落到 create_agent 内部
        # 无 project_dir 的加载，项目级 MCP 会加载不到。
        self._mcp_project = target
        self._disabled_tools = disabled_tools
        tools = await self._build_tools(wait_mcp=wait_mcp)
        # enable_memory=True：bridge 是唯一面向用户的对话入口，持久记忆只在此处 opt-in
        # （子 agent / workflow / cron 走 create_agent 默认 False，天然不带记忆）。
        self._agent, self._context = await create_agent(
            checkpoint=agents_config.checkpoint,
            project_dir=target,
            tools=tools,
            enable_memory=True,
        )
        # 注入在途审批 Broker（与 permission_engine 同源，事后赋值，零改 create_agent 签名）
        self._context.approval_broker = self._broker
        # 应用持久化的 active 供应商 (profile, model)（覆盖 config 默认模型）
        self._apply_active()
        # 本会话项目（引擎已绑定 project_dir 或退回 cwd）的 config hooks
        self._config_hooks = build_config_hooks(Path(self.workspace_dir))
        thread_id = generate_thread_id()
        recursion_limit = agents_config.recursion_limit
        self._config = RunnableConfig(
            configurable={"thread_id": thread_id},
            metadata={"workspace_dir": self.workspace_dir},
            recursion_limit=recursion_limit,
        )
        logger.info(
            f"[AgentBridge] 初始化完成, model={self.model_name}, thread={thread_id}"
        )

    async def _build_tools(self, wait_mcp: bool = False) -> list:
        """构建工具列表并记录所建于的 MCP 池版本（initialize 与轮首刷新共用）。

        基线采样固定先于构建、次序封装在本方法内：快 server 可能在构建期间完成
        加载，事后采样会把基线误记成「已就位」，会话就永远不重建了。
        """
        from lumi.agents.tools import get_tools

        self._mcp_gen = pool_generation(self._mcp_project)
        tools = await get_tools(
            disabled_tools=self._disabled_tools,
            project_dir=self._mcp_project,
            wait_mcp=wait_mcp,
        )
        if wait_mcp:
            # 阻塞路径等池完成时版本号已递增：构建后重采样，否则首轮必误判
            # 「换代」把刚建好的列表原样重建一遍（采样先行只为非阻塞路径而设）
            self._mcp_gen = pool_generation(self._mcp_project)
        return tools

    def retarget_mcp(self, target: Path | None) -> None:
        """MCP 跟踪随项目切换：改指新项目的池并强制下一轮重建工具列表（重建经
        get_tools 触发新池后台加载；不改则轮首刷新一直盯着旧项目的池）。"""
        self._mcp_project = target
        self._mcp_gen = -1
        # 子代理/workflow 经 context.project_dir 取父项目——项目状态三份一起切
        # （bridge 池跟踪 / 权限引擎在 set_workspace 已 rebase / context 快照）
        if self._context is not None:
            self._context.project_dir = target

    def mcp_pool_key(self) -> str:
        """本会话 MCP 池的 wire 标识：mcp.status 按连接过滤的匹配键（与 payload 同源投影）。"""
        return project_wire_key(self._mcp_project)

    def mcp_status_payload(self) -> dict | None:
        """本池已完成加载的状态（mcp.status 事件形状）；加载中/无结果返回 None。

        连接注册进广播通道晚于 initialize 触发的后台加载：快速完成的加载（如
        server 命令拼错毫秒级失败）会在注册前广播、无人接收，注册后据此补发。
        """
        status = get_pool_status(self._mcp_project)
        if status["loading"] or not status["servers"]:
            return None
        return {"project": self.mcp_pool_key(), "servers": status["servers"]}

    @property
    def current_thread_id(self) -> str:
        """当前会话的 thread_id"""
        if self._config is None:
            return ""
        return self._config.get("configurable", {}).get("thread_id", "")

    @property
    def workspace_dir(self) -> str:
        """本会话绑定的项目根目录（取本 bridge 引擎的项目，无引擎时退回进程 cwd）。

        项目随会话绑定后，这是会话项目的单一来源——元数据 / gateway.ready /
        system_info 都据此，而非进程级 os.getcwd()。
        """
        engine = self._context.permission_engine if self._context else None
        if engine is not None:
            return str(engine.project_dir)
        return get_workspace_dir()

    @property
    def graph(self) -> CompiledStateGraph | None:
        """底层 LangGraph 编译图实例，用于 get_state 等操作"""
        return self._agent.graph if self._agent else None

    @property
    def _memory_enabled(self) -> bool:
        """本会话是否启用持久记忆（决定 /dream 是否为内置命令）。"""
        return self._context is not None and self._context.memory_enabled

    async def delete_thread(self, thread_id: str) -> None:
        """删除指定会话的全部 checkpoint（LangGraph 会话 + 文件级 checkpoint）。"""
        if not thread_id:
            return
        from lumi.agents.runtime.checkpoint import delete_thread_checkpoint

        # adelete_thread 抛错也要清理文件级 checkpoint，否则残留孤儿目录
        try:
            if self._agent is not None:
                await self._agent.adelete_thread(thread_id)
        finally:
            await asyncio.to_thread(delete_thread_checkpoint, thread_id)
            # 该会话 thread 的持久 shell 一并回收（按 thread_id 键、会话私有）
            await get_shell_session_manager().close_session(thread_id)

    def switch_thread(self, thread_id: str) -> None:
        """切换到指定的会话线程

        Args:
            thread_id: 目标会话的 thread_id
        """
        recursion_limit = get_config().config.agents.recursion_limit
        self._config = RunnableConfig(
            configurable={"thread_id": thread_id},
            metadata={"workspace_dir": self.workspace_dir},
            recursion_limit=recursion_limit,
        )
        # 切换 checkpoint manager（复用 tracker）
        if self._shadow is not None and self._tracker is not None:
            self._shadow = FileCheckpointManager(
                thread_id, self._shadow.project_dir, self._tracker
            )
        logger.info("[AgentBridge] 切换到会话: %s", thread_id)

    # ── Folder / workspace（委派 FolderManager；folder 状态留 AgentBridge）──

    async def set_workspace(self, path: str) -> dict:
        return await self._folders.set_workspace(path)

    def add_folder(self, path: str) -> dict:
        return self._folders.add_folder(path)

    def remove_folder(self, path: str) -> dict:
        return self._folders.remove_folder(path)

    def _drain_folder_note(self) -> str:
        return self._folders.drain_folder_note()

    def _drain_ultra_note(self) -> str:
        return self._folders.drain_ultra_note()

    def add_workspace(self, directory: str) -> None:
        self._folders.add_workspace(directory)

    async def stream_response(
        self,
        content: str | list,
        tool_mode: str = "default",
        execution_mode: str = "normal",
        synthetic: bool = False,
        message_meta: dict | None = None,
        attachments: list[str] | None = None,
    ) -> AsyncGenerator[BridgeEvent, None]:
        """发送消息并 yield 事件流

        Args:
            content: 纯文本字符串或多模态 content blocks 列表。
            tool_mode: 工具审批模式（default / accept_edits / privileged）。
            execution_mode: 执行模式（normal / plan / readonly / 自定义）。
            synthetic: 系统合成轮（后台任务通知等）：声明无可显示（items: []）、
                不建 Rewind checkpoint、不注入 folder/ultra 提醒。
            message_meta: UI 侧渲染元数据（IM 渠道的 per-消息 items 等），挂到
                HumanMessage.additional_kwargs["lumi"] 随 checkpoint 持久化，
                不进模型可见文本。
            attachments: 文件附件的后端本机路径（desktop 经 wire files 参数 /
                飞书下载后传入）。与上传图片存盘路径合并：模型侧拼
                <attached-file> 标签块注入，显示侧写进 items 的 files。
        """
        # 上传图片统一存盘（与普通文件附件合流，交 read/vision 按路径消费）。
        # 放在最前：checkpoint 标签 / items 文本都基于已归一的 content。
        from lumi.gateway.uploads import persist_image_blocks

        content, image_paths = await persist_image_blocks(content)

        # 新一轮对话，清理上一轮残留的 agent 追踪状态
        self._active_agent_runs.clear()
        # tool_mode 是 context（运行时共享、可变）真相源：本轮 UI 选择写入，运行中经
        # set_tool_mode 改它即对后续工具实时生效。不进 input_data（state 快照改不动）。
        self._context.tool_mode = tool_mode
        if synthetic:
            msg = synthetic_human_message(content)
        else:
            msg = self._build_user_message(
                content, message_meta, (attachments or []) + image_paths
            )
            # 在 agent 执行前创建 checkpoint（快照当前文件状态）；label 取消息的
            # 显示声明。合成轮（如后台任务通知）不创建条目，避免在 Rewind 中显示
            await self._create_checkpoint_before_turn(msg)
            # 「添加文件夹」增减与 Ultra 档位切换的边沿提醒随下一条真实用户消息注入
            # （合成轮不消费；label 来自 items 声明，注入不碰 items 故不污染 Rewind
            # 标签；reminder 一旦前置进历史即长驻且不碰系统提示词，缓存安全）。
            for note in (self._drain_folder_note(), self._drain_ultra_note()):
                if note:
                    msg = inject_text_into_message(msg, note)
        input_data = {
            "messages": [msg],
            "execution_mode": execution_mode,
        }
        async for event in self._stream(input_data):
            yield event

    @staticmethod
    def _build_user_message(
        content: str | list, message_meta: dict | None, paths: list[str]
    ) -> HumanMessage:
        """构造真实用户消息：显示声明（lumi.items）+ 附件标签块注入。

        显示规则全部在此写侧定死，读侧 _user_items 只做纯投影：
        - items：IM 渠道经 message_meta 自带（per-消息 sender/ts/text）；否则从
          content 提文本声明单条（**契约**：bridge 入口若显示 ≠ content 文本，
          必须经 message_meta["items"] 显式声明，如 stream_command——此 fallback
          只服务 content 即显示文本的常规消息）。
        - 附件挂载：单条挂该条；多条（合并轮媒体归属未知）追加无名条目。
        - ts：消息级到达时刻（毫秒）落 meta；单条且自身无 ts 时下沉到该条目
          （IM per-item ts 更精确，不覆盖）。
        - 附件路径拼 <attached-file> 标签块经 inject_text_into_message 前置——
          纯模型侧约定（agent 用 read 读取），显示侧只认 items。
        """
        meta = {"ts": int(time.time() * 1000), **(message_meta or {})}
        items = meta.get("items")
        if items is None:
            text = extract_text_content(content).strip()
            items = [{"text": text}] if text else []
        if paths:
            files = [{"path": p, "name": Path(p).name} for p in paths]
            if len(items) == 1:
                items = [{**items[0], "files": files}]
            else:
                items = [*items, {"files": files}]
        if len(items) == 1 and "ts" not in items[0]:
            items = [{**items[0], "ts": meta["ts"]}]
        meta["items"] = items
        msg = HumanMessage(content=content, additional_kwargs={LUMI_META_KEY: meta})
        if paths:
            tags = "\n".join(
                f"<{ATTACHED_FILE_TAG}>{p}</{ATTACHED_FILE_TAG}>" for p in paths
            )
            msg = inject_text_into_message(msg, tags + "\n")
        return msg

    def set_tool_mode(self, mode: str) -> dict:
        """运行中实时切换工具审批模式（用户仅切顶部选择器、不发消息时经此路径）。

        直接改共享 context 的 tool_mode，对当前运行轮的**后续**工具调用立即生效；
        已挂起的那一个审批仍由用户手动决定（不追溯）。
        """
        self._context.tool_mode = mode
        return {"tool_mode": mode}

    # ── 模型供应商 profile（委派 ProviderService）──

    def _apply_active(self) -> None:
        self._providers.apply_active()

    def set_effort(self, provider_id: str, model: str, level: str) -> dict:
        return self._providers.set_effort(provider_id, model, level)

    def set_classifier(self, provider_id: str, model: str) -> dict:
        return self._providers.set_classifier(provider_id, model)

    def set_titler(self, provider_id: str, model: str) -> dict:
        return self._providers.set_titler(provider_id, model)

    def list_providers(self) -> dict:
        return self._providers.list_providers()

    async def test_provider(self, base_url: str, api_key: str, model: str) -> dict:
        return await self._providers.test_provider(base_url, api_key, model)

    def set_provider(self, provider_id: str, model: str) -> dict:
        return self._providers.set_provider(provider_id, model)

    def save_provider(self, profile: dict) -> dict:
        return self._providers.save_provider(profile)

    def delete_provider(self, provider_id: str) -> dict:
        return self._providers.delete_provider(provider_id)

    def list_commands(self) -> list[dict]:
        """列出当前可用的斜杠命令（技能命令），供前端补全菜单使用。

        数据源为项目技能目录，随项目动态变化——前端不硬编码，始终向后端拉取。
        dream 系仅本会话启用记忆（主对话入口）时提供，且按载体分流（IM 长会话
        只见 /dream-session，desktop 短会话只见 /dream）。
        """
        return available_commands(
            self._memory_enabled, channel=is_channel_thread(self.current_thread_id)
        )

    async def stream_command(
        self,
        name: str,
        extra_text: str = "",
        tool_mode: str = "default",
        message_meta: dict | None = None,
    ) -> AsyncGenerator[BridgeEvent, None]:
        """执行技能斜杠命令并 yield 事件流。

        查表拿到 skill.prompt，按统一约定构建结构化消息后复用 stream_response。

        Args:
            name: 技能名称（不含前导 "/"）。
            extra_text: 用户在命令后追加的文本。
            tool_mode: 工具审批模式（default / accept_edits / privileged）。
            message_meta: UI 侧渲染元数据（IM 渠道的发送者/时间戳），随 checkpoint 持久化。
        """
        # 命令路径统一设 thread 归属：内置命令分支（dream）不经 stream_response（current_thread_id
        # 在那里才 set），缺了它后台 task 的完成通知会失归属（entry.thread_id=""）。skill 分支
        # 随后又走 stream_response 重复设、同值无害。
        current_thread_id.set(self.current_thread_id)
        # dream 系仅在启用记忆的会话里是内置命令，且与 list_commands 同一载体分流
        # （/dream 仅 desktop 短会话、/dream-session 仅 IM 长会话）；条件不满足落到
        # 下方 skill 分发。同名 skill 被内置命令屏蔽是期望行为（内置优先）。
        is_channel = is_channel_thread(self.current_thread_id)
        if name == "dream" and self._memory_enabled and not is_channel:
            async for event in self._stream_dream_command():
                yield event
            return
        if name == "dream-session" and self._memory_enabled and is_channel:
            async for event in self._stream_dream_command(session_only=True):
                yield event
            return
        if name == "compact":
            async for event in self._stream_compact_command():
                yield event
            return
        if name == "goal":
            async for event in self._stream_goal_command(
                extra_text, tool_mode, message_meta
            ):
                yield event
            return

        from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector

        skill = next(
            (s for s in SkillChangeDetector.get_instance().peek() if s.name == name),
            None,
        )
        if skill is None:
            yield BridgeEvent(kind=EventKind.ERROR, error=f"未知命令: /{name}")
            return

        content = skill.prompt
        if extra_text:
            content = f"{content}\n\n{extra_text}"
        blocks = build_skill_command_blocks(name, content, extra_text)
        # 显示声明：desktop 无 message_meta 时声明「/名 输入」单条（content 是
        # 命令 wire 格式，纯给模型看）；IM 渠道自带 items（用户敲的原文）原样用
        message_meta = dict(message_meta or {})
        if "items" not in message_meta:
            message_meta["items"] = [{"text": f"/{name} {extra_text}".strip()}]
        async for event in self.stream_response(
            blocks, tool_mode=tool_mode, message_meta=message_meta
        ):
            yield event

    async def _stream_dream_command(
        self, *, session_only: bool = False
    ) -> AsyncGenerator[BridgeEvent, None]:
        """/dream 与 /dream-session：取当前会话完整历史，触发后台 dream，回一条提示消息。

        ``session_only=False``（/dream）综合「当前会话完整 message + 其他近期会话 grep」；
        ``session_only=True``（/dream-session）只综合当前会话。当前会话历史从 checkpoint
        取（命令本身尚未入 state，正好不污染）；后台跑、完成走 bg-task 通知。
        """
        from lumi.agents.memory.dream import start_dream, start_dream_session

        messages = await self.snapshot_messages()
        starter = start_dream_session if session_only else start_dream
        text = await starter(
            self._context, messages, self.workspace_dir, self.current_thread_id
        )
        async for event in self._emit_text_message(text):
            yield event

    async def snapshot_messages(self, thread_id: str = "") -> list:
        """读某 thread checkpoint 的消息快照（缺省当前 thread；无图 / 无 config 时空列表）。

        thread_id 显式给定时不依赖 bridge 当前指向——后台任务（标题生成）捕获
        发送时的 tid，用户随后切换会话也读得到正确的 thread。
        """
        if self.graph is None or self._config is None:
            return []
        config = (
            {"configurable": {"thread_id": thread_id}} if thread_id else self._config
        )
        snap = await self.graph.aget_state(config)
        return list((snap.values or {}).get("messages", [])) if snap else []

    async def compact_thread(self) -> bool:
        """对当前 thread 的空闲历史强制压缩一次（不流式、不触发模型对话）。

        供 IM 渠道每日 dream 后的 summary 阶段调用：读 checkpoint 快照 → 复用 summarizer
        的压缩核（``run_summary``）→ 经 ``aupdate_state`` 把单条摘要 carrier 写回 checkpoint
        （走 add_messages reducer，压缩后历史 = ``[System?, Human(<summary>)]``，下条真实
        用户消息到来时由 context_inject 全量重建上下文），全程不经 astream_events 故不
        外泄到渠道。

        返回是否真的压缩了（会话太短 / 末条非干净 AI 回复 / 无摘要提示词时跳过并返回 False）。
        """
        from lumi.agents.core.preprocessing.compact import (
            build_compacted_update,
            run_summary,
            select_for_compaction,
        )

        if self._context is None:
            return False
        selected = select_for_compaction(await self.snapshot_messages())
        if selected is None:
            return False
        to_summarize, last = selected

        prompt = get_config().load_prompt("SUMMARY")
        if not prompt:
            logger.warning("[compact_thread] 未配置 SUMMARY.md，跳过压缩")
            return False

        token_config = get_config().config.token
        summary_text, _ = await run_summary(
            to_summarize,
            prompt,
            tools=self._context.tools,
            system_prompt=self._context.system_prompt,
            model_name=self._context.model_name,
            max_retry=token_config.summary_ptl_retry_max,
            drop_ratio=token_config.summary_ptl_retry_drop_ratio,
        )
        update = build_compacted_update(to_summarize, last, summary_text)
        # as_node 显式指定：不依赖 LangGraph 从末次 checkpoint 推断写入者；
        # CallModel 的条件边对无 tool_calls 的末条（压缩后为摘要 carrier）路由
        # OnAgentStop，不派生工具任务
        await self.graph.aupdate_state(self._config, update, as_node="CallModel")
        logger.info(
            "[compact_thread] 已压缩 thread=%s（%d 条历史 → 摘要）",
            self.current_thread_id,
            len(to_summarize),
        )
        return True

    async def _emit_text_message(self, text: str) -> AsyncGenerator[BridgeEvent, None]:
        """把一段文本作为一条完整助手消息 yield（命令回执共用的四事件收尾）。"""
        yield BridgeEvent(kind=EventKind.MESSAGE_START)
        yield BridgeEvent(kind=EventKind.MESSAGE_DELTA, text=text)
        yield BridgeEvent(kind=EventKind.MESSAGE_COMPLETE)
        yield BridgeEvent(kind=EventKind.TURN_COMPLETE)

    async def _stream_compact_command(self) -> AsyncGenerator[BridgeEvent, None]:
        """/compact：强制压缩当前会话历史（保留摘要），回一条结果消息。"""
        try:
            compacted = await self.compact_thread()
            text = (
                "✨ 对话已压缩"
                if compacted
                else "本会话暂不需要压缩（历史较短，或末条不是完整回复）。"
            )
        except Exception:
            logger.error(
                "[/compact] 压缩失败 thread=%s", self.current_thread_id, exc_info=True
            )
            text = "压缩本会话历史时出错，请稍后再试。"
        async for event in self._emit_text_message(text):
            yield event

    async def _stream_goal_command(
        self, extra_text: str, tool_mode: str, message_meta: dict | None
    ) -> AsyncGenerator[BridgeEvent, None]:
        """/goal：设定 / 解除 / 回显会话目标。三形态由 extra_text 区分。

        - ``clear`` → 清 sidecar goal（用空值 update_meta 保留 pin/rename），回执。
        - 空 → 回显当前目标（无目标时提示用法），纯 UI 消息不进历史。
        - 条件 → 写 sidecar + 激活轮：注入激活 reminder 作驱动消息跑一整轮，前端显示
          ``/goal <条件>``。该轮结束触发 OnAgentStop → goal_stop_hook 立即评估。
        """
        from lumi.agents.core.hooks.goal import ACTIVATION_REMINDER
        from lumi.sessions.session_meta import get_goal, update_meta

        thread_id = self.current_thread_id

        # 条件 → 激活轮（走 stream_response 跑一整轮，自成一路提前返回）
        if extra_text and extra_text != "clear":
            update_meta(thread_id, goal=extra_text)
            message_meta = dict(message_meta or {})
            message_meta.setdefault("items", [{"text": f"/goal {extra_text}".strip()}])
            async for event in self.stream_response(
                ACTIVATION_REMINDER.format(condition=extra_text),
                tool_mode=tool_mode,
                message_meta=message_meta,
            ):
                yield event
            return

        # clear / 裸回显 → 纯 UI 回执，共用同一 emit 尾部
        if extra_text == "clear":
            update_meta(thread_id, goal="")
            text = "🎯 已解除目标。"
        else:
            current = get_goal(thread_id)
            text = (
                f"🎯 当前目标：{current}\n运行 `/goal clear` 解除。"
                if current
                else "当前没有设定目标。用 `/goal <条件>` 设定一个会话目标。"
            )
        async for event in self._emit_text_message(text):
            yield event

    def drain_notifications(self, thread_id: str) -> list[str]:
        """取出精确归属该 thread 的后台任务完成通知。

        多 WS 连接与渠道 poller 共享同一进程级队列，按归属认领才不会把
        别的会话的任务通知抢走。
        """
        return get_task_registry().notification_queue.drain_for(thread_id)

    def has_notifications(self, thread_id: str) -> bool:
        """是否有归属该 thread 的待发送通知——轮询方取锁前的廉价快查。

        按 thread 快查与按 thread 认领配套：全局非空但都归属别的会话时，
        轮询方不该为此空抢一次 run 锁。
        """
        return get_task_registry().notification_queue.has_for(thread_id)

    def drain_notification_hint(self, thread_id: str) -> str:
        """取出后台任务完成通知并拼成注入提示文本；无通知返回空串。"""
        notifications = self.drain_notifications(thread_id)
        if not notifications:
            return ""
        return compose_notification_hint(notifications)

    async def close(self) -> None:
        """清理本实例持有的资源。

        MCP / 后台任务等真正进程级共享资源由 shutdown_shared_runtime() 在进程退出时
        统一关闭——一个进程承载多条 WS 连接，单连接断开不能拆除其它连接还在用的它们。
        但本会话当前 thread 的持久 shell 是会话私有（按 thread_id 键），断连即回收，否则
        长跑 serve 会按 thread 累积孤儿 bash 进程（shell 改为按会话分后的回收点）。
        """
        if self._agent is not None:
            await self._agent.aclose()
        await get_shell_session_manager().close_session(self.current_thread_id)

    # 网络瞬态错误：流式传输中途断连可自动重试
    _TRANSIENT_NETWORK_ERRORS = (
        httpx.RemoteProtocolError,
        httpx.ConnectError,
        httpx.ReadError,
    )

    async def _stream(self, input_data) -> AsyncGenerator[BridgeEvent, None]:
        """核心流式处理 - yield BridgeEvent

        Args:
            input_data: 输入数据（消息或 Command）
        """
        # 本轮内注册的后台任务归属当前 thread，使完成通知能路由回本会话
        current_thread_id.set(self.current_thread_id)
        try:
            if self._agent is None or self._config is None:
                yield BridgeEvent(
                    kind=EventKind.ERROR, error="Agent 未初始化，请重启 Lumi"
                )
                return
            graph = self._agent.graph

            # 注入本会话的授权目录来源 + config hooks 到当前 run 上下文：filesystem/bash
            # 工具按 contextvar 取范围，使同进程多会话并发各 run 互不串扰、不被彼此重建
            # 进程全局所清洗（见 permissions.workspace 两层来源说明）。降级（无引擎）兜底
            # 逻辑与 cron 共用 set_run_authorized_source_for。
            engine = self._context.permission_engine if self._context else None
            set_run_authorized_source_for(engine, self._extra_folders)
            set_run_config_hooks(self._config_hooks)

            # MCP 池后台就位/换代后，轮首重建工具列表（后台加载不阻塞就绪的配套拼图：
            # 会话秒开，工具在池加载完成后的下一轮自然可用；配置作废换代同理）
            if (
                self._context is not None
                and pool_generation(self._mcp_project) != self._mcp_gen
            ):
                self._context.tools = await self._build_tools()
                logger.info(
                    "[AgentBridge] MCP 池换代，工具列表已重建（%d 个工具）",
                    len(self._context.tools),
                )

            # 检测残留图状态（待执行节点但无中断），自动恢复
            await self._recover_stale_state(graph)

            for attempt in range(MAX_STREAM_RETRIES + 1):
                try:
                    # 首次使用原始 input；重试时传 None，
                    # LangGraph 从 checkpoint 恢复执行待定节点
                    stream_input = input_data if attempt == 0 else None
                    try:
                        async for event in graph.astream_events(
                            stream_input,
                            self._config,
                            context=self._context,
                            version="v2",  # 锁死版本：on_custom_event 浮现 + parent_ids 依赖此契约
                        ):
                            kind = event.get("event", "")
                            run_id = event.get("run_id", "")
                            parent_ids = event.get("parent_ids", [])

                            parent_id = self._resolve_subagent_parent(
                                run_id, parent_ids
                            )

                            # agent 工具 run 登记（放在匹配之后，
                            # 确保 agent 自身的 on_tool_start 不会自匹配）
                            self._track_agent_run(kind, event.get("name", ""), run_id)

                            # 压缩节点(Summarizer)内部的摘要 LLM 调用：不作为 message.* 流出
                            # （astream_events 会把它逐字浮现成 on_chat_model_stream，否则摘要
                            # 全文会泄漏成助手回答），改用 compaction.status 驱动「正在压缩」指示。
                            # 对齐 claude-code：压缩调用内部消费 + 'compacting' 状态，不进用户流。
                            if kind.startswith("on_chat_model") and (
                                event.get("metadata", {}).get("langgraph_node")
                                == "Summarizer"
                            ):
                                # start→压缩开始、end/error→结束；stream 直接丢弃（摘要不外泄）
                                if kind != "on_chat_model_stream":
                                    yield BridgeEvent(
                                        kind=EventKind.COMPACTING,
                                        data={"active": kind == "on_chat_model_start"},
                                    )
                                continue

                            if kind == "on_chat_model_start":
                                yield BridgeEvent(
                                    kind=EventKind.MESSAGE_START,
                                    parent_run_id=parent_id,
                                )

                            elif kind == "on_chat_model_stream":
                                chunk = event.get("data", {}).get("chunk")
                                if chunk:
                                    usage = self._extract_usage(chunk)
                                    thinking = self._extract_thinking_from_chunk(chunk)
                                    if thinking:
                                        yield BridgeEvent(
                                            kind=EventKind.THINKING_DELTA,
                                            text=thinking,
                                            usage_metadata=usage,
                                            parent_run_id=parent_id,
                                        )
                                    text = self._extract_text_from_chunk(chunk)
                                    if text:
                                        yield BridgeEvent(
                                            kind=EventKind.MESSAGE_DELTA,
                                            text=text,
                                            usage_metadata=usage,
                                            parent_run_id=parent_id,
                                        )
                                    elif not thinking and self._has_tool_call_chunk(
                                        chunk
                                    ):
                                        yield BridgeEvent(
                                            kind=EventKind.TOOL_GENERATING,
                                            usage_metadata=usage,
                                            parent_run_id=parent_id,
                                        )

                            elif kind == "on_chat_model_end":
                                output = event.get("data", {}).get("output")
                                usage = self._extract_usage(output) if output else None
                                yield BridgeEvent(
                                    kind=EventKind.MESSAGE_COMPLETE,
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
                                # 未注入 tool_call_id 的工具（如 bash）回退到 run_id：
                                # run_id 每次执行唯一，且 on_tool_start/end 共享同一个，
                                # 避免前端按空 id 把多个工具输出匹配混淆。
                                tool_call_id = self._resolve_tool_call_id(
                                    name, tool_call_id, run_id
                                )
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
                                # 普通工具回退 run_id（与 on_tool_start 对齐）
                                tool_call_id = self._resolve_tool_call_id(
                                    name, tool_call_id, run_id
                                )

                                yield BridgeEvent(
                                    kind=EventKind.TOOL_COMPLETE,
                                    name=name,
                                    output=str(output) if output else "",
                                    tool_call_id=tool_call_id,
                                    parent_run_id=parent_id,
                                    run_id=run_id if name == "agent" else "",
                                )

                            elif kind == "on_tool_error":
                                # 工具抛异常时 LangGraph 发 on_tool_error（而非 on_tool_end），
                                # ToolNode 的 handle_tool_errors 随后生成 error ToolMessage 续跑。
                                # 不在此收尾的话前端工具行会永远卡在运行态——故补发一个标记
                                # is_error 的 TOOL_COMPLETE，让前端结束该行并红色高亮。
                                name = event.get("name", "unknown")
                                err = event.get("data", {}).get("error", "")
                                # Command 冒泡等控制流不是真失败，跳过不报错。
                                if isinstance(err, GraphBubbleUp):
                                    continue
                                inp = event.get("data", {}).get("input", {})
                                args_tcid = (
                                    inp.get("tool_call_id", "")
                                    if isinstance(inp, dict)
                                    else ""
                                )
                                tool_call_id = self._resolve_tool_call_id(
                                    name, args_tcid, run_id
                                )
                                yield BridgeEvent(
                                    kind=EventKind.TOOL_COMPLETE,
                                    name=name,
                                    output=f"工具执行失败: {err}",
                                    is_error=True,
                                    tool_call_id=tool_call_id,
                                    parent_run_id=parent_id,
                                    run_id=run_id if name == "agent" else "",
                                )

                            elif (
                                kind == "on_custom_event"
                                and event.get("name") == LUMI_APPROVAL_EVENT
                            ):
                                # 在途审批：节点 / ask 工具经 broker 发出的审批请求在此浮现
                                # 成卡片（内联流出，节点随后才挂起）。parent_run_id 复用流式
                                # 归属——子 / 外部 agent 的审批白嫖 custom event 自带的 parent_ids。
                                data = event.get("data", {}) or {}
                                if data.get("type") == "ask":
                                    approval_evt = BridgeEvent(
                                        kind=EventKind.CLARIFY,
                                        data=data,
                                        parent_run_id=parent_id,
                                    )
                                else:  # tool_approval：bridge 层富化权限评估 / 选项
                                    approval_evt = BridgeEvent(
                                        kind=EventKind.APPROVAL,
                                        data=self._enrich_tool_approval(data),
                                        parent_run_id=parent_id,
                                    )
                                # 留底供 WS 重连重发（节点续跑/被拒时由 resolve/reject 清理）
                                aid = data.get("approval_id", "")
                                if aid:
                                    self._pending_approval_events[aid] = approval_evt
                                yield approval_evt
                    finally:
                        # 清除 rewind 或恢复时设置的 checkpoint_id，
                        # 确保后续 aget_state 获取最新 checkpoint
                        self._config["configurable"].pop("checkpoint_id", None)

                    # 流正常结束，退出重试循环
                    break

                except self._TRANSIENT_NETWORK_ERRORS as e:
                    if attempt >= MAX_STREAM_RETRIES:
                        raise
                    wait = RETRY_BASE_WAIT * (attempt + 1)
                    logger.warning(
                        "[AgentBridge] 网络瞬态错误 (%s)，%ds 后重试 (%d/%d)",
                        type(e).__name__,
                        wait,
                        attempt + 1,
                        MAX_STREAM_RETRIES,
                    )
                    # 结束 TUI 中未完成的 assistant message，避免残留碎片
                    yield BridgeEvent(kind=EventKind.MESSAGE_COMPLETE)
                    # 用 STREAM_TOKEN 显示重试提示（不使用 ERROR，因为它会终止 run）
                    retry_msg = (
                        f"\n\n*网络连接中断，{wait}s 后自动重试 "
                        f"({attempt + 1}/{MAX_STREAM_RETRIES})…*\n\n"
                    )
                    yield BridgeEvent(kind=EventKind.MESSAGE_DELTA, text=retry_msg)
                    yield BridgeEvent(kind=EventKind.MESSAGE_COMPLETE)
                    await asyncio.sleep(wait)

            # 流正常结束（在途审批不再跨流中断）→ 收尾 turn.complete
            yield await self._turn_complete_event()

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

    @staticmethod
    def _resolve_tool_call_id(name: str, args_tcid: str, run_id: str) -> str:
        """解析工具对外的 wire tool_call_id。

        用注入的 tool_call_id，缺失时回退到 run_id（每次执行唯一，on_tool_start/end
        共享同一个）。在途审批后工具单次执行、不再跨 resume 重发，无需稳定 id。
        """
        return args_tcid or run_id

    def _track_agent_run(self, kind: str, name: str, run_id: str) -> None:
        """agent 工具开始时登记 run_id，供子代理事件归属（_resolve_subagent_parent）。

        工具结束时刻意不移除：后台子代理在 agent 工具立即返回后仍继续产生事件
        （asyncio.create_task 继承父 run 上下文），移除会令其祖先匹配落空、事件以
        parent_id="" 泄漏进主流（截断主回复气泡、散落工具卡）。归属按祖先链匹配，
        保留已结束的 run_id 不会误挂无关事件；集合每轮开始时清空。
        """
        if kind == "on_tool_start" and name == "agent":
            self._active_agent_runs.add(run_id)

    def _resolve_subagent_parent(self, run_id: str, parent_ids: list[str]) -> str:
        """事件的子代理归属：祖先链中「最浅」的活跃 agent run，无则空串。

        parent_ids 为 root→直接父顺序（见 langchain event_stream._get_parent_ids），
        故正序首个命中的活跃 run 即主 agent 直接派生的顶层子代理。多层委派时孙及
        更深活动据此统一归并到该顶层子代理，避免从无序集合任取导致的随机错挂/丢弃。
        排除自身 run_id，避免 agent 工具的 on_tool_start 把自己误判为子代理事件。
        """
        active = self._active_agent_runs
        # 无活跃子代理（普通对话的绝大多数事件）/ 无祖先链时直接早退，
        # 避免在 token 级流式热路径上对 parent_ids 空跑生成器。
        if not active or not parent_ids:
            return ""
        return next(
            (pid for pid in parent_ids if pid != run_id and pid in active),
            "",
        )

    async def _turn_complete_event(self) -> BridgeEvent:
        """流结束后的收尾事件：从 state 末条 AI message 取完整 usage（含 cache 详情）。

        在途审批不再跨流中断，故流跑完即一轮结束，无需检测挂起态。
        """
        try:
            state = await self._agent.graph.aget_state(self._config)
            usage = self._extract_last_ai_usage(state)
        except Exception as e:
            logger.error(
                "[AgentBridge] 取 turn.complete usage 失败: %s", e, exc_info=True
            )
            usage = None
        return BridgeEvent(kind=EventKind.TURN_COMPLETE, usage_metadata=usage)

    # ── 权限评估（委派 ApprovalEnricher）──

    def _enrich_tool_approval(self, data: dict) -> dict:
        return self._approval.enrich_tool_approval(data)

    def add_allow_rule(self, tool_expr: str) -> None:
        self._approval.add_allow_rule(tool_expr)

    # ── 在途审批应答（委派 ApprovalBroker）──

    def resolve_approval(self, approval_id: str, value) -> bool:
        """会话层收到 resume 应答时唤醒挂起的审批 / 提问（非流式控制路径）。

        value 形状沿用原 interrupt resume 值：tool_approval 为 dict
        {decision, message?, set_tool_mode?}；ask 为答案字符串 / ASK_CANCELLED。
        返回是否命中一个未决请求（未命中=审批已被 stop/切会话收尾）。
        """
        self._pending_approval_events.pop(approval_id, None)
        return self._broker.resolve(approval_id, value)

    def reject_pending(self) -> int:
        """以拒绝收尾当前轮全部挂起审批（stop / 切会话）——本轮干净完成、保留历史。

        返回处理数；为 0 表示当前无挂起审批（轮在流生成中途），调用方据此回退到硬取消。
        """
        self._pending_approval_events.clear()
        return self._broker.reject_all()

    def pending_approval_events(self) -> list[BridgeEvent]:
        """当前挂起审批/澄清的对外事件快照，供 WS 重连后重发卡片（顺序即发出顺序）。"""
        return list(self._pending_approval_events.values())

    # ── 残留状态恢复 ──

    async def _recover_stale_state(self, graph: CompiledStateGraph) -> None:
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
        self, graph: CompiledStateGraph, state
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

    @staticmethod
    def _extract_cp_ids(state) -> tuple[str, str]:
        """从 state 中提取 checkpoint_id 和 parent checkpoint_id。"""
        configurable = state.config.get("configurable", {})
        cp_id = configurable.get("checkpoint_id", "")
        parent_cp_id = ""
        parent_config = state.parent_config
        if parent_config:
            parent_cp_id = parent_config.get("configurable", {}).get(
                "checkpoint_id", ""
            )
        return cp_id, parent_cp_id

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
    def _extract_thinking_from_chunk(chunk) -> str:
        """从 LangChain chunk 中提取思考增量。

        OpenAI 方言模型经 DialectChatOpenAI 保留在 additional_kwargs
        （reasoning_content）；Anthropic 在 content 的 thinking 块中。
        """
        kwargs = getattr(chunk, "additional_kwargs", None) or {}
        if reasoning := kwargs.get("reasoning_content"):
            return reasoning
        content = getattr(chunk, "content", None)
        if isinstance(content, list):
            return "".join(
                item.get("thinking", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "thinking"
            )
        return ""

    # ── File-level Checkpoint（委派 CheckpointService）──

    def init_checkpoint(self, project_dir: Path) -> None:
        self._checkpoint.init_checkpoint(project_dir)

    async def _create_checkpoint_before_turn(self, msg: HumanMessage) -> None:
        await self._checkpoint.create_checkpoint_before_turn(msg)

    async def list_checkpoints(self) -> list[CheckpointInfo]:
        return await self._checkpoint.list_checkpoints()

    async def rewind_to_checkpoint(
        self, checkpoint: CheckpointInfo
    ) -> tuple[bool, str]:
        return await self._checkpoint.rewind_to_checkpoint(checkpoint)

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

"""Lumi TUI 主应用"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Key

from lumi import __version__
from lumi.agents.cron.delivery import DeliveryManager, TUIDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.tui.agent_bridge import AgentBridge, BridgeEvent, EventKind
from lumi.tui.run_state import RunContext, RunPhase
from lumi.tui.theme import APP_CSS, LUMI_DARK_THEME, LUMI_LIGHT_THEME, get_color
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.title_block import TitleBlock
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.input_bar import ChatInput, InputBar
from lumi.tui.widgets.command_result_panel import CommandResultPanel
from lumi.tui.widgets.thinking_indicator import ThinkingIndicator
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.user_message import UserMessage
from lumi.tui.screens.init_flow_screen import InitFlowScreen
from lumi.tui.screens.settings_screen import SettingsScreen
from lumi.utils.config import GlobalConfig, GlobalConfigManager, get_config
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id

from lumi.tui.slash_commands.registry import CommandRegistry
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.parser import parse_command_input
from lumi.tui.slash_commands.handlers import make_skill_handler
from lumi.agents.tools.skill_detector import SkillChangeDetector


class LumiApp(App):
    """Lumi TUI 主应用"""

    CSS = APP_CSS
    TITLE = "Lumi"
    BINDINGS = [
        Binding("escape", "cancel_generation", "Cancel", priority=True),
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("ctrl+s", "open_settings", "Settings", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(LUMI_DARK_THEME)
        self.register_theme(LUMI_LIGHT_THEME)
        self.theme = "lumi-dark"  # 默认暗色，on_mount 中根据全局配置切换
        self._bridge = AgentBridge()
        self._run = RunContext()
        self._thinking: ThinkingIndicator | None = None
        self._last_ctrl_c: float = 0.0
        self._global_config = None
        self._scheduler: Scheduler | None = None
        self._delivery: DeliveryManager | None = None
        self._interrupted: bool = False
        self._command_registry = CommandRegistry()
        self._pending_system_commands: list[str] = []

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield CommandResultPanel()
        yield InputBar(id="input-area")

    async def _detect_system_theme(self) -> bool:
        """检测系统主题，返回 True 表示暗色。

        macOS 通过 `defaults read -g AppleInterfaceStyle` 检测：
        - 返回 "Dark" → 暗色 (True)
        - 命令失败（亮色模式下该 key 不存在）→ 亮色 (False)
        非 macOS 平台默认暗色。
        """
        if sys.platform != "darwin":
            return True
        try:
            proc = await asyncio.create_subprocess_exec(
                "defaults",
                "read",
                "-g",
                "AppleInterfaceStyle",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            return stdout.decode().strip().lower() == "dark"
        except asyncio.TimeoutError:
            logger.debug("[LumiApp] 系统主题检测超时，使用暗色主题")
            return True
        except FileNotFoundError:
            logger.debug("[LumiApp] 'defaults' 命令不可用，使用暗色主题")
            return True
        except Exception:
            logger.warning(
                "[LumiApp] 系统主题检测意外失败，使用暗色主题", exc_info=True
            )
            return True

    async def _apply_theme_mode(self, mode: str) -> None:
        """根据 theme_mode 设置主题。

        Args:
            mode: 主题模式，可选值为 "dark"、"light"、"system"。
        """
        if mode == "dark":
            self.theme = "lumi-dark"
        elif mode == "light":
            self.theme = "lumi-light"
        else:
            # system 模式：检测一次系统主题
            is_dark = await self._detect_system_theme()
            logger.info("系统主题检测结果: dark=%s", is_dark)
            self.theme = "lumi-dark" if is_dark else "lumi-light"

    async def on_mount(self) -> None:
        # 加载全局配置
        self._global_config = GlobalConfigManager.load()

        # 首次启动引导：initialized 为 False 时触发引导流程
        if not self._global_config.initialized:
            self.push_screen(InitFlowScreen(), callback=self._on_init_flow_done)
            return

        # 已初始化，直接完成启动
        await self._finish_mount()

    async def _on_init_flow_done(self, config: GlobalConfig) -> None:
        """初始化引导完成后的回调。"""
        self._global_config = config
        await self._finish_mount()

    async def _finish_mount(self) -> None:
        """应用主题、注入环境变量并初始化 Agent bridge。"""
        await self._apply_theme_mode(self._global_config.theme_mode)

        # 注入 config.yaml 中的 env 环境变量
        try:
            get_config().apply_env()
        except Exception as e:
            logger.warning(f"注入环境变量失败: {e}")

        try:
            await self._bridge.initialize()
        except Exception as e:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_error("初始化失败:", str(e))
            return

        # TitleBlock 挂载到 ChatLog 内部，随聊天内容一起滚动
        chat_log = self.query_one(ChatLog)
        recent_sessions = await self._load_recent_sessions()
        title = TitleBlock(
            model_name=self._bridge.model_name,
            recent_sessions=recent_sessions,
            id="title-block",
        )
        title.border_title = f"Lumi v{__version__}"
        await chat_log.mount(title)

        # 初始化斜杠命令系统
        self._init_slash_commands()

        # 初始化铃铛未读数
        self._refresh_bell()

        # 初始化定时任务子系统
        try:
            cron_dir = GLOBAL_CONFIG_DIR / "cron"
            job_store = JobStore(cron_dir / "jobs.json")
            run_log = RunLog(cron_dir / "runs")
            delivery = DeliveryManager()
            delivery.register(TUIDelivery(self))
            scheduler = Scheduler(job_store, run_log, delivery)
            init_cron_tool(scheduler, job_store, run_log)
            await scheduler.start()
            self._scheduler = scheduler
            self._delivery = delivery
            logger.info("[LumiApp] 定时任务子系统已启动")
        except Exception:
            logger.warning("[LumiApp] 定时任务子系统启动失败", exc_info=True)
            self.notify("定时任务子系统启动失败，cron 功能不可用", severity="warning")

    async def _load_recent_sessions(self, limit: int = 3) -> list:
        """加载最近的历史会话摘要。

        Args:
            limit: 最大返回数量

        Returns:
            SessionSummary 列表，加载失败返回空列表
        """
        graph = self._bridge.graph
        if graph is None or graph.checkpointer is None:
            return []
        try:
            from lumi.tui.session_store import list_sessions

            return await list_sessions(
                graph,
                current_thread_id=self._bridge.current_thread_id,
                limit=limit,
            )
        except Exception:
            logger.debug("[LumiApp] 加载最近会话失败", exc_info=True)
            return []

    # ── 斜杠命令 ──

    def _init_slash_commands(self) -> None:
        """初始化斜杠命令系统：注册内置命令和技能命令。"""

        builtins: list[tuple[str, str, Callable[..., Awaitable[None]]]] = [
            ("skills", "查看所有可用技能", lambda _="": self._open_skills_screen()),
            ("resume", "恢复历史会话", lambda _="": self._open_resume_screen()),
            ("cron", "查看和管理定时任务", lambda _="": self._open_cron_screen()),
            (
                "cron-notify",
                "查看定时任务通知",
                lambda _="": self._open_cron_notify_screen(),
            ),
            ("agents", "查看所有可用 Agent", lambda _="": self._open_agents_screen()),
            ("mcp", "查看 MCP 服务器状态", lambda _="": self._open_mcp_screen()),
            (
                "clear",
                "清空对话历史，开始新会话",
                lambda _="": self._clear_conversation(),
            ),
        ]
        for name, desc, handler in builtins:
            self._command_registry.register(
                SlashCommand(
                    name=name,
                    description=desc,
                    command_type=CommandType.BUILTIN,
                    handler=handler,
                )
            )

        # 加载技能命令
        self._sync_skill_commands()

        # 注入命令注册表到 InputBar
        self.query_one(InputBar).set_command_registry(self._command_registry)

    def _sync_skill_commands(self) -> None:
        """同步技能命令到注册表。"""
        try:
            skills = SkillChangeDetector.get_instance().peek()
            self._command_registry.sync_skills(
                skills,
                lambda skill: make_skill_handler(skill, self._send_skill_to_agent),
            )
        except Exception:
            logger.warning("[LumiApp] 技能命令同步失败", exc_info=True)

    async def _send_skill_to_agent(
        self, skill_name: str, content: str, extra_text: str = ""
    ) -> None:
        """技能命令的 send_to_agent 回调：构建结构化消息发送给 Agent。

        消息格式：
            Block 0: <command-name>/xxx</command-name><command-type>skill</command-type>
            Block 1: <skill-content>{prompt}</skill-content>
            Block 2 (可选): <user-input>{extra_text}</user-input>

        Args:
            skill_name: 技能名称
            content: skill.prompt（可能拼接了 extra_text）
            extra_text: 用户在斜杠命令后追加的原始文本
        """
        meta = (
            f"<command-name>/{skill_name}</command-name>"
            f"<command-type>skill</command-type>"
        )
        skill_block = f"<skill-content>{content}</skill-content>"

        blocks: list[dict[str, str]] = [
            {"type": "text", "text": meta},
            {"type": "text", "text": skill_block},
        ]
        if extra_text:
            blocks.append(
                {"type": "text", "text": f"<user-input>{extra_text}</user-input>"}
            )

        self._run.phase = RunPhase.IDLE
        self.query_one(InputBar).set_disabled(True)
        self._run.task = asyncio.create_task(self._run_stream(blocks))

    # ── 会话恢复 ──

    async def _open_resume_screen(self) -> None:
        """打开会话恢复选择界面。"""
        from lumi.tui.session_store import list_sessions

        # 检查 checkpoint 模式是否支持持久化
        checkpoint_mode = get_config().config.agents.checkpoint
        if checkpoint_mode == "memory":
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint(
                "● ",
                "当前 checkpoint 模式为 memory，会话不会持久化。"
                "请在 config.yaml 中设置 agents.checkpoint: sqlite 以启用会话恢复。",
            )
            return

        graph = self._bridge.graph
        if graph is None:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "Agent 未初始化，无法恢复会话")
            return

        sessions = await list_sessions(
            graph,
            current_thread_id=self._bridge.current_thread_id,
        )
        if not sessions:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "没有可恢复的历史会话")
            return

        from lumi.tui.screens.resume_screen import ResumeScreen

        self.push_screen(ResumeScreen(sessions), callback=self._on_resume_done)

    async def _on_resume_done(self, thread_id: str | None) -> None:
        """会话恢复选择完成后的回调。

        切换 thread_id，从 StateSnapshot 中读取历史消息并重新渲染到 ChatLog。

        Args:
            thread_id: 用户选择的 thread_id，取消时为 None
        """
        if thread_id is None:
            return

        self._bridge.switch_thread(thread_id)

        # 清空当前聊天界面
        chat_log = self.query_one(ChatLog)
        await chat_log.remove_children()

        title = TitleBlock(
            model_name=self._bridge.model_name,
            id="title-block",
        )
        title.border_title = f"Lumi v{__version__}"
        await chat_log.mount(title)

        # 从 StateSnapshot 中恢复历史消息
        await self._restore_messages(thread_id, chat_log)

        await chat_log.append_hint("● ", f"已恢复会话 {thread_id[:16]}...")
        await chat_log.scroll_to_end()

        # 重置运行状态
        self._run.reset()
        self._interrupted = False

    # ── 清空对话 ──

    async def _clear_conversation(self) -> None:
        """清空当前对话，生成新 thread_id 开始新会话。"""
        new_tid = generate_thread_id()
        self._bridge.switch_thread(new_tid)

        chat_log = self.query_one(ChatLog)
        await chat_log.remove_children()

        title = TitleBlock(
            model_name=self._bridge.model_name,
            id="title-block",
        )
        title.border_title = f"Lumi v{__version__}"
        await chat_log.mount(title)

        await chat_log.append_hint("● ", "已开始新会话")
        await chat_log.scroll_to_end()

        self._run.reset()
        self._interrupted = False

    # ── 技能列表 ──

    async def _open_skills_screen(self) -> None:
        """打开技能列表界面。"""
        detector = SkillChangeDetector.get_instance()
        skills, _ = detector.check()

        if not skills:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "暂无可用技能")
            return

        from lumi.tui.screens.skills_screen import SkillsScreen

        self.push_screen(SkillsScreen(skills), callback=self._on_skills_done)

    async def _on_skills_done(self, skill_name: str | None) -> None:
        """技能列表界面关闭回调。

        Args:
            skill_name: 选中的技能名称，取消时为 None。
        """

    # ── Agent 列表 ──

    async def _open_agents_screen(self) -> None:
        """打开 Agent 列表界面。"""
        from lumi.agents.tools.config import load_agents

        agents = load_agents()

        if not agents:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "暂无可用 Agent")
            return

        from lumi.tui.screens.agents_screen import AgentsScreen

        self.push_screen(AgentsScreen(agents), callback=self._on_agents_done)

    async def _on_agents_done(self, agent_name: str | None) -> None:
        """Agent 列表界面关闭回调。

        Args:
            agent_name: 选中的 Agent 名称，取消时为 None。
        """

    # ── 定时任务管理 ──

    async def _open_cron_screen(self) -> None:
        """打开定时任务管理界面。"""
        if self._scheduler is None:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "定时任务子系统未启动，/cron 不可用")
            return

        jobs = await self._scheduler.get_all_jobs()

        from lumi.tui.screens.cron_screen import CronScreen

        self.push_screen(
            CronScreen(jobs, on_delete=self._delete_cron_job),
            callback=self._on_cron_done,
        )

    async def _delete_cron_job(self, job_id: str) -> None:
        """执行实际的 cron 任务删除。

        Args:
            job_id: 要删除的任务 ID。
        """
        if self._scheduler is None:
            logger.warning("[LumiApp] 无法删除任务 %s: 调度器未初始化", job_id)
            return
        job = await self._scheduler.get_job(job_id)
        name = job.name if job else job_id
        await self._scheduler.delete_job(job_id)
        chat_log = self.query_one(ChatLog)
        await chat_log.append_hint("● ", f"已删除定时任务「{name}」({job_id})")

    async def _on_cron_done(self, result: str | None) -> None:
        """定时任务管理界面关闭回调。

        Args:
            result: "changed" 表示有删除操作，None 表示无变更。
        """
        if result == "changed":
            self._refresh_bell()

    # ── 定时任务通知 ──

    async def _open_cron_notify_screen(self) -> None:
        """打开定时任务通知界面。"""
        from lumi.tui.widgets.notification_panel import NotificationStore

        store = NotificationStore()
        records = store.load()

        if not records:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "暂无通知")
            return

        from lumi.tui.screens.cron_notify_screen import CronNotifyScreen

        self.push_screen(
            CronNotifyScreen(records, store=store),
            callback=self._on_cron_notify_done,
        )

    async def _on_cron_notify_done(self, result: str | None) -> None:
        """通知界面关闭回调，更新铃铛未读数。

        Args:
            result: "changed" 表示有变更，None 表示无变更。
        """
        if result == "changed":
            self._refresh_bell()

    # ── MCP 状态 ──

    async def _open_mcp_screen(self) -> None:
        """打开 MCP 服务器状态界面。"""
        from lumi.agents.tools.providers.mcp import get_mcp_session_manager

        manager = get_mcp_session_manager()
        servers = manager.get_server_info()

        if not servers:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "未配置任何 MCP 服务器")
            return

        from lumi.tui.screens.mcp_screen import MCPScreen

        self.push_screen(MCPScreen(servers), callback=self._on_mcp_done)

    async def _on_mcp_done(self, result: str | None) -> None:
        """MCP 界面关闭回调。"""

    def _refresh_bell(self) -> None:
        """从 NotificationStore 刷新铃铛未读数。"""
        try:
            from lumi.tui.widgets.notification_panel import NotificationStore

            records = NotificationStore().load()
            unread = sum(1 for r in records if not r.read)
        except Exception:
            logger.warning("[LumiApp] 通知记录加载失败", exc_info=True)
            return
        try:
            self.query_one(InputBar).update_bell(unread)
        except NoMatches:
            logger.debug("[LumiApp] InputBar 尚未挂载，跳过铃铛更新")
        except Exception:
            logger.warning("[LumiApp] 铃铛更新失败, unread=%s", unread, exc_info=True)

    # 工具被拒绝/中断时的输出关键词
    _TOOL_REJECT_KEYWORDS: frozenset[str] = frozenset(
        {
            "用户拒绝了工具执行",
            "用户中断了工具调用请求",
            "User declined to answer questions",
        }
    )

    # 从 system-reminder 中提取 command-name 的正则
    _COMMAND_NAME_RE: re.Pattern[str] = re.compile(
        r"<command-name>(/[\w-]+)</command-name>"
    )

    # 从消息中提取 user-input 的正则
    _USER_INPUT_RE: re.Pattern[str] = re.compile(
        r"<user-input>(.*?)</user-input>", re.DOTALL
    )

    async def _restore_messages(self, thread_id: str, chat_log: ChatLog) -> None:
        """从 checkpoint 恢复历史消息并渲染到 ChatLog。

        处理 human、ai（含 tool_calls）和 tool 类型消息。
        先收集所有 tool 消息的输出，再按顺序渲染，确保 ToolBlock 能匹配到输出。

        Args:
            thread_id: 会话线程 ID
            chat_log: 聊天日志组件
        """
        graph = self._bridge.graph
        if graph is None:
            return

        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await graph.aget_state(config)
            if not snapshot or not snapshot.values:
                return

            messages = snapshot.values.get("messages", [])

            # 预先收集所有 tool 消息的输出，key 为 tool_call_id
            tool_outputs: dict[str, str] = {}
            for msg in messages:
                msg_type = getattr(msg, "type", None)
                if msg_type == "tool":
                    tc_id = getattr(msg, "tool_call_id", None)
                    content = getattr(msg, "content", "")
                    if tc_id:
                        tool_outputs[tc_id] = self._extract_text_content(content)

            for msg in messages:
                msg_type = getattr(msg, "type", None) or (
                    msg.get("type") if isinstance(msg, dict) else None
                )
                content = getattr(msg, "content", None) or (
                    msg.get("content", "") if isinstance(msg, dict) else ""
                )

                if msg_type == "human":
                    display = self._extract_human_display_text(content)
                    if display:
                        await chat_log.mount(UserMessage(display))

                elif msg_type == "ai":
                    # 渲染文本内容
                    text = self._extract_text_content(content)
                    if text:
                        assistant_msg = AssistantMessage()
                        await chat_log.mount(assistant_msg)
                        assistant_msg.append_token(text)
                        assistant_msg.finalize()

                    # 渲染 tool_calls
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    for tc in tool_calls:
                        name = tc.get("name", "unknown")
                        args = tc.get("args", {})
                        tc_id = tc.get("id", "")
                        block = ToolBlock(name, args)
                        await chat_log.mount(block)
                        output = tool_outputs.get(tc_id, "")
                        if output in self._TOOL_REJECT_KEYWORDS:
                            block.set_error(output)
                        else:
                            block.set_done(output)

                # tool 类型消息已通过 tool_outputs 映射处理，跳过

        except Exception as e:
            logger.warning("恢复历史消息失败: %s", e, exc_info=True)
            await chat_log.append_error("恢复历史消息失败:", str(e))

    def _extract_human_display_text(self, content: str | list) -> str:
        """从 human 消息中提取用于显示的文本。

        技能命令消息从 <command-name> 和 <user-input> 标签还原用户输入，
        如 "/media-digest 介绍下这个"。
        非技能消息则过滤掉所有 system-reminder 块，返回剩余纯文本。

        Args:
            content: 字符串或多模态 content blocks 列表

        Returns:
            用于显示的文本
        """
        raw = self._extract_text_content(content)

        # 从 command-name + user-input 还原用户输入
        cmd_match = self._COMMAND_NAME_RE.search(raw)
        if cmd_match:
            cmd = cmd_match.group(1)
            ui_match = self._USER_INPUT_RE.search(raw)
            if ui_match:
                user_input = ui_match.group(1).strip()
                return f"{cmd} {user_input}" if user_input else cmd
            return cmd

        # 非技能消息：过滤 system-reminder 块，只保留用户实际输入
        cleaned = re.sub(
            r"<system-reminder>.*?</system-reminder>\s*",
            "",
            raw,
            flags=re.DOTALL,
        ).strip()
        return cleaned or raw

    @staticmethod
    def _extract_text_content(content: str | list) -> str:
        """从消息 content 中提取纯文本。

        Args:
            content: 字符串或多模态 content blocks 列表

        Returns:
            提取的文本内容
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return ""

    # ── 输入处理 ──

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._run.is_running:
            return

        await self._try_dismiss_command_panel()

        text = event.text
        tool_mode = event.tool_mode
        images = event.images
        chat_log = self.query_one(ChatLog)

        await chat_log.mount(UserMessage(text, image_count=len(images)))
        await chat_log.auto_scroll_if_needed()

        # 斜杠命令路由
        if text.startswith("/"):
            command_name, extra_text = parse_command_input(text)
            command = self._command_registry.get(command_name)
            if command:
                try:
                    await command.handler(extra_text)
                    # system 命令记录到待注入列表，下次 human message 时告知模型
                    if command.command_type == CommandType.BUILTIN:
                        self._pending_system_commands.append(f"/{command_name}")
                except Exception as e:
                    logger.error(
                        "[LumiApp] 命令 /%s 执行失败", command_name, exc_info=True
                    )
                    await chat_log.append_error(f"/{command_name} 执行失败:", str(e))
                return

        # 构建 content：有图片时使用多模态 content blocks
        if images:
            content: str | list = [{"type": "text", "text": text}]
            for img in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.media_type};base64,{img.data}"
                        },
                    }
                )
        else:
            content = text

        # 中断提示：告知 LLM 上一轮回复被用户中断
        if self._interrupted:
            interrupted_hint = (
                "<system-reminder>\n"
                "The user interrupted the conversation before the previous reply was completed.\n"
                "</system-reminder>\n"
            )
            if isinstance(content, list):
                content.insert(0, {"type": "text", "text": interrupted_hint})
            else:
                content = [
                    {"type": "text", "text": interrupted_hint},
                    {"type": "text", "text": content},
                ]
            self._interrupted = False

        # 注入待告知的系统命令（用户在上次对话间执行的 /skills、/resume 等）
        if self._pending_system_commands:
            hints = "".join(
                f"<command-name>{cmd}</command-name><command-type>system</command-type>\n"
                for cmd in self._pending_system_commands
            )
            if isinstance(content, list):
                content.insert(0, {"type": "text", "text": hints})
            else:
                content = [
                    {"type": "text", "text": hints},
                    {"type": "text", "text": content},
                ]
            self._pending_system_commands.clear()

        self._run.phase = RunPhase.IDLE  # 即将启动
        self.query_one(InputBar).set_disabled(True)

        self._run.task = asyncio.create_task(self._run_stream(content, tool_mode))

    async def _run_stream(
        self, content: str | list, tool_mode: str = "approve"
    ) -> None:
        await self._consume_events(self._bridge.stream_response(content, tool_mode))

    async def _run_resume(self, value) -> None:
        self._run.task = asyncio.create_task(
            self._consume_events(self._bridge.stream_resume(value))
        )

    async def _consume_events(self, event_stream) -> None:
        chat_log = self.query_one(ChatLog)
        try:
            async for evt in event_stream:
                await self._apply_event(evt, chat_log)
        except Exception as e:
            logger.error(f"[TUI] 事件流异常: {e}", exc_info=True)
            await self._show_error(chat_log, str(e))

    # ── 状态机 ──

    _THINKING_PHASES = {RunPhase.THINKING, RunPhase.TOOL_CALL_PENDING}

    def _transition(self, evt: BridgeEvent) -> tuple[RunPhase, RunPhase]:
        """纯逻辑状态转换，不操作 DOM。返回 (old, new)。"""
        old = self._run.phase
        match evt.kind:
            case EventKind.MODEL_START:
                new = RunPhase.THINKING
            case EventKind.STREAM_TOKEN:
                new = RunPhase.STREAMING
            case EventKind.MODEL_END:
                new = old  # 不改变可见状态
            case EventKind.TOOL_CALL_CHUNK:
                new = RunPhase.TOOL_CALL_PENDING
            case EventKind.TOOL_START:
                new = RunPhase.TOOL_RUNNING
            case EventKind.TOOL_END:
                new = RunPhase.TOOL_RUNNING
            case EventKind.ASK:
                new = RunPhase.WAITING_ASK
            case EventKind.TOOL_APPROVAL:
                new = RunPhase.WAITING_APPROVAL
            case EventKind.DONE | EventKind.ERROR:
                new = RunPhase.IDLE
            case _:
                new = old
        self._run.phase = new
        return old, new

    async def _apply_event(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        """两阶段事件处理：先做状态转换驱动的 UI 切换，再执行事件特定逻辑。"""
        old, new = self._transition(evt)

        # 阶段离开 thinking → 移除 indicator（下次进入时重新 mount 到正确位置）
        if old in self._THINKING_PHASES and new not in self._THINKING_PHASES:
            if self._thinking:
                self._thinking.teardown()
                self._thinking = None

        # 阶段进入 thinking → 显示 indicator
        if new in self._THINKING_PHASES and old not in self._THINKING_PHASES:
            await self._ensure_thinking(chat_log)

        # 离开 STREAMING → finalize assistant message
        if old == RunPhase.STREAMING and new != RunPhase.STREAMING:
            self._finalize_assistant_msg()

        # 事件特定 UI 更新
        match evt.kind:
            case EventKind.STREAM_TOKEN:
                await self._handle_stream_token(evt, chat_log)
            case EventKind.TOOL_START:
                await self._handle_tool_start(evt, chat_log)
            case EventKind.TOOL_END:
                await self._handle_tool_end(evt, chat_log)
            case EventKind.ASK:
                await self._handle_ask(evt, chat_log)
            case EventKind.TOOL_APPROVAL:
                await self._handle_tool_approval(evt, chat_log)
            case EventKind.DONE:
                self._finish_run()
            case EventKind.ERROR:
                await self._show_error(chat_log, evt.error)

    # ── 事件 handlers（已解耦 thinking 管理）──

    async def _handle_stream_token(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        if self._run.assistant_msg is None:
            self._run.assistant_msg = AssistantMessage()
            await chat_log.mount(self._run.assistant_msg)
        self._run.assistant_msg.append_token(evt.text)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_start(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        key = evt.tool_call_id or evt.name
        # 审批模式下 ToolBlock 已在 TOOL_APPROVAL 阶段创建
        if key not in self._run.tool_blocks:
            block = ToolBlock(evt.name, evt.args or {}, approval_mode=evt.approval_mode)
            self._run.tool_blocks[key] = block
            await chat_log.mount(block)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_end(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        key = evt.tool_call_id or evt.name
        block = self._run.tool_blocks.pop(key, None)
        # Fallback: tool_call_id 可能在 TOOL_START/END 间不一致
        if block is None:
            for k, b in self._run.tool_blocks.items():
                if b._name == evt.name:
                    block = self._run.tool_blocks.pop(k)
                    break
        if block:
            block.set_done(evt.output)
        await chat_log.auto_scroll_if_needed()

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        tool_call_id = (evt.data or {}).get("tool_call_id", "")
        key = tool_call_id or "ask"
        block = self._run.tool_blocks.get(key)
        # Fallback: TOOL_START 中 InjectedToolCallId 可能不在事件 input 中，
        # 导致 ToolBlock 以工具名 "ask" 为 key 存储
        if block is None:
            for k, b in self._run.tool_blocks.items():
                if b._name == "ask":
                    block = b
                    break
        if block:
            dialog = AskDialog(evt.data)
            await block.mount_interactive(dialog)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_approval(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        self._finalize_assistant_msg()
        # 保存工具调用信息，供拒绝/取消时创建 ToolBlock
        tool_calls = (evt.data or {}).get("tool_calls", [])
        self._run.last_approval_tool_calls = tool_calls
        for tc in tool_calls:
            key = tc.get("id") or tc.get("name", "unknown")
            self._run.tool_blocks.pop(key, None)
        approval = ToolApproval(evt.data)
        await chat_log.mount(approval)
        await chat_log.auto_scroll_if_needed()

    # ── 辅助方法 ──

    def _finalize_assistant_msg(self) -> None:
        if self._run.assistant_msg:
            self._run.assistant_msg.finalize()
            self._run.assistant_msg = None

    async def _ensure_thinking(self, chat_log: ChatLog) -> None:
        """在 chat log 底部显示 ThinkingIndicator。

        始终 teardown 旧实例并重新 mount，确保位置在最新内容之后。
        """
        if self._thinking:
            self._thinking.teardown()
        self._thinking = ThinkingIndicator()
        await chat_log.mount(self._thinking)
        await chat_log.auto_scroll_if_needed()

    async def _show_error(self, chat_log: ChatLog, error: str) -> None:
        if len(error) > 300:
            error = error[:300] + "..."
        await chat_log.append_error("Error:", error)
        self._finish_run()

    # ── 中断恢复 ──

    async def on_ask_dialog_answered(self, event: AskDialog.Answered) -> None:
        from lumi.agents.tools.providers.ask import ASK_CANCELLED

        if event.answer == ASK_CANCELLED:
            # 取消时补充视觉反馈，与 tool_approval cancel 一致
            chat_log = self.query_one(ChatLog)
            block = self._run.tool_blocks.get("ask")
            if block:
                block.set_error("User declined to answer questions")
            await chat_log.auto_scroll_if_needed()
        await self._run_resume(event.answer)

    async def on_tool_approval_decided(self, event: ToolApproval.Decided) -> None:
        decision = event.decision
        # 拒绝或取消时，创建标记为错误的 ToolBlock 保留视觉记录
        if decision in ("reject", "cancel"):
            chat_log = self.query_one(ChatLog)
            tool_calls = getattr(event, "_tool_calls", None)
            # 从最近的 ToolApproval 数据中恢复工具信息
            if tool_calls is None:
                tool_calls = self._run.last_approval_tool_calls
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                block = ToolBlock(name, args)
                await chat_log.mount(block)
                msg = (
                    "用户中断了审批" if decision == "cancel" else "用户拒绝了此工具执行"
                )
                block.set_error(msg)
            await chat_log.auto_scroll_if_needed()
        await self._run_resume(decision)

    # ── 操作 ──

    def add_notification(
        self,
        job_name: str,
        output: str,
        started_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> None:
        try:
            from lumi.tui.widgets.notification_panel import (
                NotificationRecord,
                NotificationStore,
            )

            store = NotificationStore()
            records = store.load()
            record = NotificationRecord.create(
                job_name, output, started_at=started_at, duration_ms=duration_ms
            )
            records.insert(0, record)
            if len(records) > 100:
                records = records[:100]
            store.save(records)
        except Exception:
            logger.warning("[LumiApp] 保存通知失败: job=%s", job_name, exc_info=True)
            return
        self._refresh_bell()

    async def on_command_result_panel_dismissed(
        self, event: CommandResultPanel.Dismissed
    ) -> None:
        """命令结果面板关闭时，在 ChatLog 中追加状态行。"""
        await self._append_dismiss_status(event.command_name)

    async def _try_dismiss_command_panel(self) -> bool:
        """若命令结果面板可见则关闭，返回是否关闭了面板。"""
        panel = self.query_one(CommandResultPanel)
        if panel.is_visible:
            name = panel.hide()
            await self._append_dismiss_status(name)
            return True
        return False

    async def _append_dismiss_status(self, command_name: str) -> None:
        """在 ChatLog 末尾追加面板关闭状态行。"""
        chat_log = self.query_one(ChatLog)
        await chat_log.append_hint("└ ", "对话框已关闭")

    def _finish_run(self) -> None:
        if self._thinking:
            self._thinking.teardown()
            self._thinking = None
        self._run.reset()
        try:
            self.query_one(InputBar).set_disabled(False)
        except Exception:
            logger.error("[LumiApp] 无法重新启用输入栏，UI 可能已损坏", exc_info=True)

    async def action_open_settings(self) -> None:
        """打开设置界面。"""
        if self._global_config is None:
            self._global_config = GlobalConfigManager.load()
        self.push_screen(
            SettingsScreen(self._global_config), callback=self._on_settings_done
        )

    async def _on_settings_done(self, result: GlobalConfig | None) -> None:
        """设置界面关闭后的回调。"""
        if result is not None:
            self._global_config = result
            await self._apply_theme_mode(result.theme_mode)

    async def action_cancel_generation(self) -> None:
        # 如果当前有 pushed screen（如 ResumeScreen、SettingsScreen），
        # dismiss 当前 screen 而非执行取消生成逻辑
        if len(self.screen_stack) > 1:
            self.screen.dismiss(None)
            return

        if await self._try_dismiss_command_panel():
            return

        # 如果当前有审批组件，esc 触发审批中断而非取消生成
        try:
            approval = self.query_one(ToolApproval)
            approval.post_message(ToolApproval.Decided("cancel"))
            approval.call_later(approval.remove)
            return
        except NoMatches:
            pass

        # 如果当前有 AskDialog，esc 触发拒绝回答
        try:
            dialog = self.query_one(AskDialog)
            dialog._decline()
            return
        except NoMatches:
            pass

        if self._run.is_running:
            if self._run.task and not self._run.task.done():
                self._run.task.cancel()
            self._finalize_assistant_msg()
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint(
                "● ",
                "Interrupted",
                style=f"dim {get_color('error')}",
            )
            self._interrupted = True
            self._finish_run()

    async def action_quit_app(self) -> None:
        try:
            if self._scheduler:
                await self._scheduler.stop()
            if self._delivery:
                await self._delivery.close_all()
            await self._bridge.close()
        except Exception:
            logger.warning("[LumiApp] 关闭资源时出错", exc_info=True)
        self.exit()

    async def action_handle_ctrl_c(self) -> None:
        """Ctrl+C: 输入框有内容时清空；空框时 1.5 秒内再按一次退出。"""
        import time

        try:
            inp = self.query_one("#user-input", ChatInput)
        except NoMatches:
            await self.action_quit_app()
            return

        # 输入框有内容 → 清空文本，重置退出计时
        if inp.value:
            inp.value = ""
            self._last_ctrl_c = 0.0
            return

        # 文本已空但有待发送图片 → 清空图片，重置退出计时
        input_bar = self.query_one(InputBar)
        if input_bar.has_pending_images:
            input_bar.clear_images()
            self._last_ctrl_c = 0.0
            return

        # 输入框和图片都已空 → 判断是否双击退出
        now = time.monotonic()
        if now - self._last_ctrl_c < 1.5:
            await self.action_quit_app()
            return
        self._last_ctrl_c = now
        input_bar.show_exit_hint()

    def on_key(self, event: Key) -> None:
        """任意非 Ctrl+C 按键重置双击退出窗口。"""
        if event.key != "ctrl+c":
            if self._last_ctrl_c:
                self._last_ctrl_c = 0.0
                try:
                    self.query_one(InputBar).hide_exit_hint()
                except NoMatches:
                    pass

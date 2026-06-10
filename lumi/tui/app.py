"""Lumi TUI 主应用"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Key
from textual.widget import Widget

from lumi import __version__
from lumi.agents.cron.delivery import DeliveryManager, TUIDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.agents.bridge import AgentBridge, BridgeEvent, build_skill_command_blocks
from lumi.tui.run_state import RunContext, RunPhase
from lumi.tui.subagent_tracker import SubagentTracker
from lumi.tui.theme import APP_CSS, LUMI_DARK_THEME, LUMI_LIGHT_THEME, get_color
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.plan_approval import PlanApproval
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.title_block import TitleBlock
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.input_bar import ChatInput, InputBar
from lumi.tui.widgets.command_result_panel import CommandResultPanel
from lumi.tui.widgets.run_status_bar import RunStatusBar
from lumi.tui.widgets.status_line import StatusLine
from lumi.tui.widgets.todos_bar import TodosBar
from lumi.tui.widgets.tool_block import ToolBlock, ToolStatus
from lumi.tui.widgets.user_message import UserMessage
from lumi.tui.screens.init_flow_screen import InitFlowScreen
from lumi.tui.screens.settings_screen import SettingsScreen
from lumi.utils.clipboard import copy_to_clipboard
from lumi.utils.config import GlobalConfig, GlobalConfigManager, get_config
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id
from lumi.utils.workspace_id import get_workspace_dir, get_workspace_id

from lumi.tui.event_router import EventRouter
from lumi.tui.message_restore import restore_messages
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.tui.slash_commands.registry import CommandRegistry
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.parser import parse_command_input
from lumi.tui.slash_commands.handlers import make_skill_handler
from lumi.agents.core.preprocessing.skill_detector import SkillChangeDetector
from lumi.utils.constants import NOTIFICATION_POLL_INTERVAL


class LumiApp(App):
    """Lumi TUI 主应用"""

    CSS = APP_CSS
    TITLE = "Lumi"
    BINDINGS = [
        Binding("escape", "cancel_generation", "Cancel", priority=True),
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("ctrl+s", "open_settings", "Settings", priority=True),
        Binding(
            "shift+up", "scroll_chat('up')", "Scroll Up", show=False, priority=True
        ),
        Binding(
            "shift+down",
            "scroll_chat('down')",
            "Scroll Down",
            show=False,
            priority=True,
        ),
        Binding(
            "pageup", "scroll_chat('page_up')", "Page Up", show=False, priority=True
        ),
        Binding(
            "pagedown",
            "scroll_chat('page_down')",
            "Page Down",
            show=False,
            priority=True,
        ),
        Binding("ctrl+b", "open_bg", "Background", show=False, priority=True),
    ]

    def __init__(self, *, privileged: bool = False, accept_edits: bool = False) -> None:
        super().__init__()
        self.register_theme(LUMI_DARK_THEME)
        self.register_theme(LUMI_LIGHT_THEME)
        self.theme = "lumi-dark"  # 默认暗色，on_mount 中根据全局配置切换
        self._bridge = AgentBridge()
        self._run = RunContext()
        self._subagent_tracker = SubagentTracker()
        self._assembler: WidgetAssembler | None = None
        self._last_ctrl_c: float = 0.0
        self._global_config = None
        self._scheduler: Scheduler | None = None
        self._delivery: DeliveryManager | None = None
        self._interrupted: bool = False
        self._command_registry = CommandRegistry()
        self._pending_system_commands: list[str] = []
        self._notification_poll_timer = None
        self._last_esc: float = 0.0  # 双击 Esc 检测时间戳
        self._rewind_checkpoints: dict = {}  # _open_rewind_screen 缓存
        self._todos_hidden_for_approval: bool = False  # 审批期间临时隐藏 todos-bar
        self._privileged = privileged
        self._accept_edits = accept_edits
        self._workspace_id = get_workspace_id()
        self._workspace_dir = get_workspace_dir()
        self._cron_dir = GLOBAL_CONFIG_DIR / "cron" / self._workspace_id

    def _query_safe(self, widget_type: type[Widget]) -> Widget | None:
        """按类型查询 widget，未挂载时返回 None 而非抛异常。"""
        try:
            return self.query_one(widget_type)
        except NoMatches:
            return None

    def _update_todos_bar(self, todos: list[dict]) -> None:
        """更新 #todos-bar 面板内容，并同步当前任务名到 RunStatusBar。

        无任务时隐藏；全部完成时由 _finish_run 统一清除。
        """
        bar = self._query_safe(TodosBar)
        if bar is None:
            return
        bar.update_todos(todos)

        # 提取当前 in_progress 任务名同步到 RunStatusBar
        current_task = ""
        for t in todos:
            if t.get("status") == "in_progress":
                current_task = t.get("content", "")
                break
        status_bar = self._query_safe(RunStatusBar)
        if status_bar:
            status_bar.set_task_label(current_task)

    def _clear_todos_bar(self) -> None:
        """隐藏并清空 #todos-bar 面板。"""
        bar = self._query_safe(TodosBar)
        if bar:
            bar.clear()

    def _hide_todos_bar_for_approval(self) -> None:
        """审批期间临时隐藏 todos-bar，避免遮挡审批 UI。"""
        bar = self._query_safe(TodosBar)
        if bar is None:
            return
        self._todos_hidden_for_approval = bar.has_class("-visible")
        bar.remove_class("-visible")

    def _restore_todos_bar_after_approval(self) -> None:
        """审批结束后恢复 todos-bar 的可见状态。"""
        if not self._todos_hidden_for_approval:
            return
        self._todos_hidden_for_approval = False
        bar = self._query_safe(TodosBar)
        if bar:
            bar.add_class("-visible")

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield RunStatusBar()
        yield TodosBar()
        yield CommandResultPanel()
        yield InputBar(id="input-area")
        yield StatusLine()

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

    async def _mount_title_block(
        self, chat_log: ChatLog, recent_sessions: list | None = None
    ) -> None:
        """创建并挂载 TitleBlock，统一配置 model_name 和 border_title。"""
        title = TitleBlock(
            model_name=self._bridge.model_name,
            recent_sessions=recent_sessions or [],
            id="title-block",
        )
        title.border_title = f"Lumi v{__version__}"
        await chat_log.mount(title)

    def _reset_session_state(self) -> None:
        """重置会话状态（reset_session + interrupted + StatusLine 刷新）。"""
        self._run.reset_session()
        if self._assembler:
            self._assembler.reset()
        self._interrupted = False
        self._pending_system_commands.clear()
        sl = self._query_safe(StatusLine)
        if sl:
            sl.refresh_display()

    async def _finish_mount(self) -> None:
        """应用主题、注入环境变量并初始化 Agent bridge。"""
        import os

        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
            logger.info(
                "检测到 SSH 环境。如遇鼠标问题，请使用 --no-mouse 标志或设置 LUMI_NO_MOUSE=1"
            )
        # 初始化 WidgetAssembler（ChatLog 已在 compose 中创建）
        self._assembler = WidgetAssembler(self.query_one(ChatLog))
        # 绑定 RunContext 到 RunStatusBar，使 spinner tick 可读取实时状态
        self.query_one(RunStatusBar).bind_run_context(self._run)

        from lumi.tui._app_lifecycle import apply_theme_mode

        await apply_theme_mode(self, self._global_config.theme_mode)

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

        # 初始化文件级 Checkpoint
        self._bridge.init_checkpoint(Path.cwd())

        # 后台清理过期 checkpoint thread 目录
        asyncio.create_task(self._cleanup_stale_checkpoints())

        # 配置 StatusLine（尝试从 OpenRouter 获取 context_length）
        await self._refresh_model_status()

        # TitleBlock 挂载到 ChatLog 内部，随聊天内容一起滚动
        chat_log = self.query_one(ChatLog)
        recent_sessions = await self._load_recent_sessions()
        await self._mount_title_block(chat_log, recent_sessions=recent_sessions)

        # 初始化斜杠命令系统
        self._init_slash_commands()

        # 初始化铃铛未读数
        self._refresh_bell()

        # 初始化定时任务子系统（按工作目录隔离）
        try:
            cron_dir = self._cron_dir
            cron_dir.mkdir(parents=True, exist_ok=True)
            # 写入 workspace.meta 便于调试（非关键，失败不影响 cron）
            try:
                meta_file = cron_dir / "workspace.meta"
                if not meta_file.exists():
                    meta_file.write_text(
                        json.dumps(
                            {
                                "path": self._workspace_dir,
                                "created_at": datetime.now().isoformat(),
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
            except OSError:
                logger.debug("workspace.meta 写入失败（非关键）", exc_info=True)
            job_store = JobStore(cron_dir / "jobs.json")
            run_log = RunLog(cron_dir / "runs")
            delivery = DeliveryManager()
            delivery.register(TUIDelivery(self))
            scheduler = Scheduler(
                job_store,
                run_log,
                delivery,
                on_job_status=self._on_cron_job_status,
            )
            init_cron_tool(scheduler, job_store, run_log)
            await scheduler.start()
            self._scheduler = scheduler
            self._delivery = delivery
            logger.info(
                "[LumiApp] 定时任务子系统已启动 (workspace=%s)", self._workspace_id
            )
        except Exception:
            logger.warning("[LumiApp] 定时任务子系统启动失败", exc_info=True)
            self.notify("定时任务子系统启动失败，cron 功能不可用", severity="warning")

        # 启动后台任务通知轮询
        self._notification_poll_timer = self.set_interval(
            NOTIFICATION_POLL_INTERVAL, self._poll_notifications
        )

        # 启动后台任务指示器更新
        self._bg_indicator_timer = self.set_interval(1.0, self._update_bg_indicator)

        # 绑定用户自定义快捷键
        self._bind_custom_keys(self._global_config)

        # 设置 privileged 模式（CLI --privileged-danger）
        if self._privileged:
            self.query_one(InputBar).set_privileged(True)
        elif self._accept_edits:
            self.query_one(InputBar).set_accept_edits(True)

    def _bind_custom_keys(self, config: GlobalConfig) -> None:
        """根据配置绑定用户自定义快捷键。"""
        kb = config.keybindings
        try:
            self.bind(kb.copy_selection, "copy_selection", description="复制选中文本")
        except Exception:
            logger.warning(
                f"[LumiApp] 绑定快捷键失败: copy_selection={kb.copy_selection}",
                exc_info=True,
            )

    async def _cleanup_stale_checkpoints(self) -> None:
        """后台清理过期的 checkpoint thread 目录。"""
        try:
            from lumi.agents.runtime.checkpoint import cleanup_stale_threads

            removed = await asyncio.to_thread(cleanup_stale_threads)
            if removed:
                logger.info(
                    "[LumiApp] Cleaned up %d stale checkpoint thread(s)", removed
                )
        except Exception:
            logger.warning("[LumiApp] Stale checkpoint cleanup failed", exc_info=True)

    def _get_last_assistant_raw(self) -> str | None:
        """获取最近一条 AssistantMessage 的原始文本。"""
        try:
            chat_log = self.query_one(ChatLog)
            msgs = chat_log.query(AssistantMessage)
            if msgs:
                last: AssistantMessage = msgs.last()
                return last._raw if last._has_content else None
        except NoMatches:
            pass
        return None

    async def action_copy_selection(self) -> None:
        """复制选中文本到系统剪贴板，无选中时回退到最近一条 AI 回复。"""
        text = self.screen.get_selected_text()
        if not text:
            text = self._get_last_assistant_raw()
        if not text:
            return
        ok = await asyncio.to_thread(copy_to_clipboard, text)
        try:
            self.query_one(InputBar).flash_message("Copied" if ok else "Copy failed")
        except NoMatches:
            pass

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
                workspace=self._workspace_dir,
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
            (
                "rewind",
                "回退到历史 checkpoint（恢复文件和会话）",
                lambda _="": self._open_rewind_screen(),
            ),
            ("cron", "查看和管理定时任务", lambda _="": self._open_cron_screen()),
            (
                "cron-notify",
                "查看定时任务通知",
                lambda _="": self._open_cron_notify_screen(),
            ),
            ("agents", "查看所有可用 Agent", lambda _="": self._open_agents_screen()),
            ("model", "切换 / 管理模型供应商", lambda _="": self._open_model_screen()),
            ("bg", "查看后台任务", lambda _="": self._open_bg_screen()),
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
        """技能命令的 send_to_agent 回调：构建结构化消息并启动一轮 run。

        消息格式见 build_skill_command_blocks（TUI / desktop 共用的单一事实来源）。

        Args:
            skill_name: 技能名称
            content: skill.prompt（可能拼接了 extra_text）
            extra_text: 用户在斜杠命令后追加的原始文本
        """
        blocks = build_skill_command_blocks(skill_name, content, extra_text)

        self._run.phase = RunPhase.IDLE
        self._run.start()
        input_bar = self.query_one(InputBar)
        input_bar.set_disabled(True)
        tool_mode = input_bar.tool_mode
        self._run.task = asyncio.create_task(self._run_stream(blocks, tool_mode))

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
            workspace=self._workspace_dir,
        )
        if not sessions:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "没有可恢复的历史会话")
            return

        from lumi.tui.screens.resume_screen import ResumeScreen

        self.push_screen(ResumeScreen(sessions), callback=self._on_resume_done)

    async def _on_resume_done(self, thread_id: str | None) -> None:
        """会话恢复选择完成后的回调：切换 thread_id 并重新渲染历史消息。"""
        if thread_id is None:
            return

        self._bridge.switch_thread(thread_id)

        # 清空当前聊天界面
        chat_log = self.query_one(ChatLog)
        await chat_log.remove_children()

        await self._mount_title_block(chat_log)

        # 从 StateSnapshot 中恢复历史消息
        todos = await restore_messages(self._bridge.graph, chat_log, thread_id)
        if todos:
            self._update_todos_bar(todos)
        else:
            self._clear_todos_bar()

        await chat_log.scroll_to_end()

        # 重置运行状态
        self._reset_session_state()

    # ── 清空对话 ──

    async def _clear_conversation(self) -> None:
        """清空当前对话，生成新 thread_id 开始新会话。"""
        new_tid = generate_thread_id()
        self._bridge.switch_thread(new_tid)

        chat_log = self.query_one(ChatLog)
        await chat_log.remove_children()

        await self._mount_title_block(chat_log)

        await chat_log.append_hint("● ", "已开始新会话")
        await chat_log.scroll_to_end()

        self._clear_todos_bar()
        self._reset_session_state()

    # ── Rewind ──

    async def _open_rewind_screen(self) -> None:
        """打开 rewind checkpoint 选择界面。"""
        checkpoints = await self._bridge.list_checkpoints()
        if not checkpoints:
            return

        # 缓存 checkpoint 列表，供回调使用（避免重复调用 list_checkpoints）
        self._rewind_checkpoints = {cp.commit_hash: cp for cp in checkpoints}

        # 正序展示（按时间从旧到新），初始选中最后一项（最新的 checkpoint）
        from lumi.tui.screens.rewind_screen import RewindScreen

        self.push_screen(
            RewindScreen(checkpoints, initial_index=-1),
            callback=self._on_rewind_done,
        )

    async def _on_rewind_done(self, commit_hash: str | None) -> None:
        """Rewind 选择界面关闭后的回调。"""
        if commit_hash is None:
            self._rewind_checkpoints = {}
            return

        # 从缓存中查找选中的 checkpoint
        target = self._rewind_checkpoints.get(commit_hash)
        self._rewind_checkpoints = {}

        if target is None:
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "Checkpoint not found")
            return

        # 执行 rewind
        success, warning = await self._bridge.rewind_to_checkpoint(target)

        chat_log = self.query_one(ChatLog)
        if not success:
            await chat_log.append_hint(
                "● ",
                f"Rewind failed: {warning}",
                style=f"dim {get_color('error')}",
            )
            return

        # 清空 ChatLog 并从恢复后的 state 重新渲染历史消息
        await chat_log.remove_children()

        await self._mount_title_block(chat_log)

        thread_id = self._bridge.current_thread_id
        # 使用目标 checkpoint 记录的 langgraph_checkpoint_id 读取
        # 该轮用户消息发送前的会话状态，确保恢复的消息不包含该轮及之后的内容
        # 若为空（第一条消息之前），则跳过消息恢复，聊天窗口保持空白
        self._clear_todos_bar()
        if target.langgraph_checkpoint_id:
            todos = await restore_messages(
                self._bridge.graph,
                chat_log,
                thread_id,
                checkpoint_id=target.langgraph_checkpoint_id,
            )
            if todos:
                self._update_todos_bar(todos)

        # 部分成功时显示警告（如文件已恢复但 LangGraph 会话回退失败）
        if warning:
            await chat_log.append_hint(
                "● ",
                warning,
                style=f"dim {get_color('warning')}",
            )

        await chat_log.scroll_to_end()

        # 将回退的 prompt 内容填充到输入框，用户可直接重新发送
        try:
            inp = self.query_one("#user-input", ChatInput)
            inp.value = target.label
            inp.move_cursor(inp.document.end)
        except NoMatches:
            logger.debug("[LumiApp] rewind 后未找到输入框组件")

        # 重置运行状态
        self._reset_session_state()

    # ── 列表类 Screen 通用流程 ──

    async def _push_list_screen(
        self,
        items: Sequence,
        screen_factory: Callable[[Sequence], object],
        empty_hint: str,
        callback: Callable | None = None,
    ) -> None:
        """列表类 screen 的标准打开流程：空数据提示 / push screen。"""
        if not items:
            await self.query_one(ChatLog).append_hint("● ", empty_hint)
            return
        self.push_screen(screen_factory(items), callback=callback or (lambda _: None))

    # ── 技能列表 ──

    async def _open_skills_screen(self) -> None:
        """打开技能列表界面。"""
        detector = SkillChangeDetector.get_instance()
        skills, _ = detector.check()

        from lumi.tui.screens.skills_screen import SkillsScreen

        await self._push_list_screen(
            skills, SkillsScreen, "暂无可用技能", self._on_skills_done
        )

    async def _on_skills_done(self, skill_name: str | None) -> None:
        """技能列表界面关闭回调。"""

    # ── Agent 列表 ──

    async def _open_agents_screen(self) -> None:
        """打开 Agent 列表界面。"""
        from lumi.agents.tools.loader import load_agents

        agents = load_agents()

        from lumi.tui.screens.agents_screen import AgentsScreen

        await self._push_list_screen(
            agents, AgentsScreen, "暂无可用 Agent", self._on_agents_done
        )

    async def _on_agents_done(self, agent_name: str | None) -> None:
        """Agent 列表界面关闭回调。"""

    # ── 模型供应商 ──

    async def _open_model_screen(self) -> None:
        """打开模型切换界面（仅切换；增删改在桌面端配置页）。"""
        data = self._bridge.list_providers()
        # 把「供应商 × 模型」拍平成可选项
        entries = [
            {"provider": p["id"], "name": p["name"], "model": m}
            for p in data["profiles"]
            for m in p["models"]
        ]
        if not entries:
            await self.query_one(ChatLog).append_hint(
                "● ", "暂无模型，请在桌面端「管理供应商」中配置后再切换"
            )
            return

        from lumi.tui.screens.model_screen import ModelScreen

        self.push_screen(
            ModelScreen(entries, data["active"]), callback=self._on_model_done
        )

    async def _on_model_done(self, value: str | None) -> None:
        """选中后切换模型（value = "<provider_id>\\t<model>"）并刷新状态显示。"""
        if not value:
            return
        provider, _, model = value.partition("\t")
        self._bridge.set_provider(provider, model)
        await self._refresh_model_status()

    async def _refresh_model_status(self) -> None:
        """按当前 bridge.model_name 刷新 StatusLine（含 context_length 探测）。"""
        from lumi.utils.model_info import fetch_model_info
        from lumi.utils.read_config import get_config as get_yaml_config

        model_name = self._bridge.model_name
        config_context_length = get_yaml_config().config.token.context_length
        model_info = await fetch_model_info(model_name)
        context_max = (
            model_info.context_length
            if model_info and model_info.context_length > 0
            else config_context_length
        )
        self.query_one(StatusLine).configure(
            run_ctx=self._run,
            model_name=model_name,
            context_max=context_max,
        )

    # ── 后台任务管理 ──

    async def action_open_bg(self) -> None:
        """Ctrl+B 打开后台任务面板。"""
        await self._open_bg_screen()

    async def _open_bg_screen(self) -> None:
        """打开后台任务列表界面。"""
        from lumi.agents.runtime.bg_tasks import TaskStatus, get_task_registry

        entries = [
            e for e in get_task_registry().all_tasks() if e.status == TaskStatus.RUNNING
        ]

        if not entries:
            return

        from lumi.tui.screens.bg_screen import BgScreen

        self.push_screen(BgScreen(entries), callback=lambda _: None)

    def _update_bg_indicator(self) -> None:
        """定时更新 InputBar 中的后台任务指示器。"""
        from lumi.agents.runtime.bg_tasks import (
            TaskStatus,
            get_task_registry,
        )

        try:
            bar = self.query_one(InputBar)
        except NoMatches:
            return

        entries = [
            e for e in get_task_registry().all_tasks() if e.status == TaskStatus.RUNNING
        ]
        bar.update_bg_indicator(entries)

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
        """执行实际的 cron 任务删除。"""
        if self._scheduler is None:
            logger.warning("[LumiApp] 无法删除任务 %s: 调度器未初始化", job_id)
            return
        job = await self._scheduler.get_job(job_id)
        name = job.name if job else job_id
        await self._scheduler.delete_job(job_id)
        chat_log = self.query_one(ChatLog)
        await chat_log.append_hint("● ", f"已删除定时任务「{name}」({job_id})")

    async def _on_cron_done(self, result: str | None) -> None:
        """定时任务管理界面关闭回调。"""
        if result == "changed":
            self._refresh_bell()

    # ── 定时任务通知 ──

    async def _open_cron_notify_screen(self) -> None:
        """打开定时任务通知界面。"""
        from lumi.tui.widgets.notification_panel import NotificationStore

        store = NotificationStore(self._cron_notifications_path)
        records = store.load()

        from lumi.tui.screens.cron_notify_screen import CronNotifyScreen

        await self._push_list_screen(
            records,
            lambda r: CronNotifyScreen(r, store=store),
            "暂无通知",
            self._on_cron_notify_done,
        )

    async def _on_cron_notify_done(self, result: str | None) -> None:
        """通知界面关闭回调，更新铃铛未读数。"""
        if result == "changed":
            self._refresh_bell()

    # ── MCP 状态 ──

    async def _open_mcp_screen(self) -> None:
        """打开 MCP 服务器状态界面。"""
        from lumi.agents.tools.providers.mcp import get_mcp_session_manager

        manager = get_mcp_session_manager()
        servers = manager.get_server_info()

        from lumi.tui.screens.mcp_screen import MCPScreen

        await self._push_list_screen(
            servers, MCPScreen, "未配置任何 MCP 服务器", self._on_mcp_done
        )

    async def _on_mcp_done(self, result: str | None) -> None:
        """MCP 界面关闭回调。"""

    @property
    def _cron_notifications_path(self) -> Path:
        """当前 workspace 的通知文件路径。"""
        return self._cron_dir / "notifications.json"

    def _on_cron_job_status(self, job_names: list[str]) -> None:
        """Scheduler 回调：定时任务开始/结束时更新 InputBar 指示器。"""
        try:
            self.query_one(InputBar).update_cron_status(job_names)
        except NoMatches:
            logger.debug("[LumiApp] _on_cron_job_status: InputBar 未挂载，跳过")

    def _refresh_bell(self) -> None:
        """从 NotificationStore 刷新铃铛未读数。"""
        try:
            from lumi.tui.widgets.notification_panel import NotificationStore

            records = NotificationStore(self._cron_notifications_path).load()
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

    # ── 输入处理 ──

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._run.is_running:
            return

        await self._try_dismiss_command_panel()

        text = event.text
        tool_mode = event.tool_mode
        plan_mode = event.plan_mode
        plan_reminder_pending = event.plan_reminder_pending
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
            content = self._prepend_text_block(
                content,
                "<system-reminder>\n"
                "The user interrupted the conversation before the previous reply was completed.\n"
                "</system-reminder>\n",
            )
            self._interrupted = False

        # 注入待告知的系统命令（用户在上次对话间执行的 /skills、/resume 等）
        if self._pending_system_commands:
            hints = "".join(
                f"<command-name>{cmd}</command-name><command-type>system</command-type>\n"
                for cmd in self._pending_system_commands
            )
            content = self._prepend_text_block(content, hints)
            self._pending_system_commands.clear()

        # Plan mode reminder 注入（仅首次进入 plan mode 时注入一次）
        if plan_mode and plan_reminder_pending:
            from lumi.agents.tools.providers.plan import plan_mode_response

            content = self._prepend_text_block(content, plan_mode_response)
            self.query_one(InputBar).consume_plan_reminder()

        self._run.phase = RunPhase.IDLE  # 即将启动
        self._run.start()
        self.query_one(InputBar).set_disabled(True)

        execution_mode = "plan" if plan_mode else "normal"
        self._run.task = asyncio.create_task(
            self._run_stream(content, tool_mode, execution_mode=execution_mode)
        )

    async def _run_stream(
        self,
        content: str | list,
        tool_mode: str = "default",
        execution_mode: str = "normal",
        is_meta: bool = False,
    ) -> None:
        """执行流

        Args:
            content: 用户输入内容
            tool_mode: 工具审批模式，默认为 "default"
            execution_mode: 执行模式，默认为 "normal"
            is_meta: 标记为系统生成的不可见消息（restore 时不显示）
        """
        await self._consume_events(
            self._bridge.stream_response(
                content, tool_mode, execution_mode=execution_mode, is_meta=is_meta
            )
        )

    async def _run_resume(self, value) -> None:
        # resume 前保留 agent blocks，清除 run_id 映射以便 replay 重新关联
        self._subagent_tracker.prepare_for_resume()
        self._run.task = asyncio.create_task(
            self._consume_events(self._bridge.stream_resume(value))
        )

    async def _consume_events(self, event_stream) -> None:
        chat_log = self.query_one(ChatLog)
        router = EventRouter(self._run, self._assembler, self._subagent_tracker, self)
        try:
            async for evt in event_stream:
                await router.dispatch(evt, chat_log)
                await chat_log.auto_scroll_if_needed()
                # 定期检查是否需要压缩旧消息
                chat_log.schedule_compact()
        except Exception as e:
            logger.error(f"[TUI] 事件流异常: {e}", exc_info=True)
            await self._show_error(chat_log, str(e))

    def _get_approval_anchor(self) -> Widget:
        """获取审批 widget 的挂载锚点（InputBar 之前）。"""
        return self.query_one(InputBar)

    def _hide_input_for_approval(self) -> None:
        """审批期间隐藏输入栏，审批 widget 已占据交互位置。"""
        self.query_one(InputBar).display = False

    def _restore_input_after_approval(self) -> None:
        """审批结束后恢复输入栏。"""
        self.query_one(InputBar).display = True

    def _set_tool_waiting(self, tool_call_id: str, fallback_name: str) -> None:
        """查找 ToolBlock 并设置为 WAITING 状态（ask / ExitPlanMode 共用）。"""
        key = tool_call_id or fallback_name
        block = self._assembler.tool_blocks.get(key)
        if block is None:
            result = self._assembler.find_tool_block_by_name(fallback_name)
            if result is not None:
                _, block = result
        if block:
            block.set_waiting()

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        self._set_tool_waiting((evt.data or {}).get("tool_call_id", ""), "ask")
        dialog = AskDialog(evt.data)
        self._hide_input_for_approval()
        await self.mount(dialog, before=self._get_approval_anchor())

    async def _handle_tool_approval(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        self._assembler.finalize_assistant_msg()
        # 保存完整审批数据，供决策时使用（持久化/boundary/warnings）
        self._run.last_approval_data = dict(evt.data or {})
        tool_calls = (evt.data or {}).get("tool_calls", [])
        self._run.last_approval_tool_calls = tool_calls
        for tc in tool_calls:
            key = tc.get("id") or tc.get("name", "unknown")
            self._assembler.pop_tool_block(key)
        approval = ToolApproval(evt.data)
        # 子代理审批直接挂载到 chat_log（AgentGroup 模式下无嵌套 DOM）
        if evt.parent_run_id:
            self._subagent_tracker.set_approval_context(evt.parent_run_id)
        else:
            self._subagent_tracker.clear_approval_context()
        # 审批期间临时隐藏 todos-bar，避免遮挡审批 UI
        self._hide_todos_bar_for_approval()
        self._hide_input_for_approval()
        await self.mount(approval, before=self._get_approval_anchor())

    async def _handle_exit_plan_mode(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        self._set_tool_waiting((evt.data or {}).get("tool_call_id", ""), "ExitPlanMode")
        dialog = PlanApproval(evt.data)
        self._hide_input_for_approval()
        await self.mount(dialog, before=self._get_approval_anchor())

    # ── 辅助方法 ──

    def _finalize_assistant_msg(self) -> None:
        if self._assembler:
            self._assembler.finalize_assistant_msg()

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
            block = self._assembler.tool_blocks.get("ask")
            if block:
                block.set_error("User declined to answer questions")
            await chat_log.auto_scroll_if_needed()
        self._restore_input_after_approval()
        await self._run_resume(event.answer)

    async def on_tool_approval_decided(self, event: ToolApproval.Decided) -> None:
        from lumi.agents.permissions.workspace import add_authorized_directory

        decision = event.decision
        approval_data = self._run.last_approval_data
        resume_value: dict

        # ── Approve 变体：TUI 层处理持久化，统一发送 approve ──
        if decision in ("always_allow_exact", "always_allow_pattern"):
            self._persist_allow_rule(decision, approval_data)
            resume_value = {"decision": "approve"}
        elif decision == "accept_edits_session":
            # 1. 临时授权 boundary violation 路径（与 allow_once 一致）
            for v in approval_data.get("boundary_violations", []):
                add_authorized_directory(v)
            # 2. 切换 InputBar 模式，后续消息使用 accept_edits
            self.query_one(InputBar).set_accept_edits(True)
            # 3. 同时更新正在运行的 graph state，当前 run 的后续工具调用也自动通过
            resume_value = {
                "decision": "approve",
                "set_tool_mode": "accept_edits",
            }
        elif decision in ("approve", "allow_once"):
            # 临时授权 boundary violation 路径
            for v in approval_data.get("boundary_violations", []):
                add_authorized_directory(v)
            resume_value = {"decision": "approve"}

        # ── Cancel（ESC）──
        elif decision == "cancel":
            resume_value = {
                "decision": "cancel",
                "message": "用户中断了工具调用请求",
            }
            await self._show_rejection_tool_blocks("用户中断了审批")

        # ── Reject ──
        else:
            reason = self._build_reject_reason(approval_data)
            resume_value = {"decision": "reject", "message": reason}
            await self._show_rejection_tool_blocks("用户拒绝了此工具执行")

        # 审批结束，恢复 todos-bar 和 InputBar
        self._restore_todos_bar_after_approval()
        self._restore_input_after_approval()
        # 清理审批上下文
        self._subagent_tracker.clear_approval_context()
        await self._run_resume(resume_value)

    def _persist_allow_rule(self, decision_key: str, approval_data: dict) -> None:
        """从审批数据中提取 tool_expr 并持久化到权限引擎。"""
        options = approval_data.get("options", [])
        expr = next(
            (o["tool_expr"] for o in options if o.get("key") == decision_key),
            None,
        )
        if expr:
            self._bridge.add_allow_rule(expr)
        else:
            logger.error(
                "[ToolApproval] 无法找到 tool_expr (decision=%s, options=%s)，规则未持久化",
                decision_key,
                [o.get("key") for o in options],
            )
        for v in approval_data.get("boundary_violations", []):
            self._bridge.add_workspace(v)

    @staticmethod
    def _build_reject_reason(approval_data: dict) -> str:
        """从审批数据中构建人类可读的拒绝原因。"""
        warnings = approval_data.get("warnings", [])
        if warnings:
            return "用户拒绝了工具执行: " + "; ".join(warnings)
        return "用户拒绝了工具执行"

    async def _show_rejection_tool_blocks(self, error_msg: str) -> None:
        """为拒绝/取消的工具调用创建标记为错误的 ToolBlock。"""
        chat_log = self.query_one(ChatLog)
        tool_calls = self._run.last_approval_tool_calls
        # AgentGroup 模式下，子代理审批拒绝/取消不创建 ToolBlock，
        # 错误信息由 agent TOOL_END → finish_agent_error 在 AgentGroup 中展示
        agent_block = self._subagent_tracker.get_approval_block()
        is_agent_group_mode = (
            agent_block is not None and self._assembler.agent_group is not None
        )
        if not is_agent_group_mode:
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                block = ToolBlock(name, args)
                await chat_log.mount(block)
                block.set_error(error_msg)
        await chat_log.auto_scroll_if_needed()

    async def on_plan_approval_decided(self, event: PlanApproval.Decided) -> None:
        from lumi.agents.tools.providers.plan import PLAN_REJECTED

        self._restore_input_after_approval()
        if event.decision == "rejected":
            await self._run_resume(PLAN_REJECTED)
        else:
            # Plan 被批准 → 关闭 plan mode 指示器
            self.query_one(InputBar).set_plan_mode(False)
            await self._run_resume(event.decision)

    @staticmethod
    def _prepend_text_block(content: str | list, text: str) -> list:
        """将文本块注入到 content 前端，str 自动转为 list 格式。"""
        block = {"type": "text", "text": text}
        if isinstance(content, list):
            return [block, *content]
        return [block, {"type": "text", "text": content}]

    def _sync_plan_mode_from_tool(self) -> None:
        """LLM 调用 EnterPlanMode 工具后同步 InputBar 指示器。

        reminder_pending=False 因为 tool response 已包含 reminder。
        """
        self.query_one(InputBar).set_plan_mode(True, reminder_pending=False)

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

            store = NotificationStore(self._cron_notifications_path)
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
        # 隐藏运行状态栏
        bar = self._query_safe(RunStatusBar)
        if bar:
            bar.hide()
        # 任务全部完成 → 清除 TodosBar
        try:
            todos_bar = self.query_one(TodosBar)
            if todos_bar.is_all_done:
                todos_bar.clear()
        except NoMatches:
            pass
        # 清理所有残留的运行中 ToolBlock（on_tool_end 未匹配到时的兜底）
        for block in self._assembler.tool_blocks.values():
            if block.status == ToolStatus.RUNNING:
                logger.warning(
                    "[LumiApp] ToolBlock '%s' still RUNNING at _finish_run, "
                    "marking as done (on_tool_end may have been missed)",
                    block._name,
                )
                block.set_done()
        # 兜底：确保 AgentGroup 已 finalize（中断/错误场景）
        if self._assembler.agent_group is not None:
            self._assembler.agent_group.force_finalize()
        # 刷新底部状态行（token 计数在 reset 前刷新）
        sl = self._query_safe(StatusLine)
        if sl:
            sl.refresh_display()
        self._subagent_tracker.reset()
        self._assembler.reset()
        self._run.reset()
        input_bar = self._query_safe(InputBar)
        if input_bar:
            input_bar.set_disabled(False)

    async def _poll_notifications(self) -> None:
        """轮询后台任务通知队列

        仅在 Agent 空闲（IDLE）时检查队列，有通知则作为用户消息触发新一轮 Agent 处理。
        """
        if self._run.is_running:
            return

        notifications = self._bridge.drain_notifications()
        if not notifications:
            return

        logger.info(f"[LumiApp] 收到 {len(notifications)} 条后台任务通知")
        combined = "\n".join(notifications)
        hint = f"{combined}\nRead the output file to retrieve the result."

        # 先设为 THINKING 防止下一次 poll 重入
        self._run.phase = RunPhase.THINKING
        self._run.start()
        try:
            self.query_one(InputBar).set_disabled(True)
        except NoMatches:
            logger.error("[LumiApp] 通知处理时找不到 InputBar，跳过")
            self._run.phase = RunPhase.IDLE
            return
        self._run.task = asyncio.create_task(
            self._run_stream(hint, tool_mode="default", is_meta=True)
        )

    def action_scroll_chat(self, direction: str) -> None:
        """滚动聊天日志区域，审批组件激活时委派到其内容区域。

        Args:
            direction: 滚动方向 — "up" / "down" / "page_up" / "page_down"
        """
        # 审批组件激活时，滚动其内容区域而非 ChatLog
        approval = self._query_safe(ToolApproval) or self._query_safe(PlanApproval)
        if approval is not None:
            approval.scroll_content(direction)
            return

        chat_log = self._query_safe(ChatLog)
        if chat_log is None:
            return
        if direction == "up":
            chat_log.scroll_up(animate=False)
        elif direction == "down":
            chat_log.scroll_down(animate=False)
        elif direction == "page_up":
            chat_log.scroll_page_up(animate=False)
        elif direction == "page_down":
            chat_log.scroll_page_down(animate=False)

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
            from lumi.tui._app_lifecycle import apply_theme_mode

            self._global_config = result
            await apply_theme_mode(self, result.theme_mode)

    async def action_cancel_generation(self) -> None:
        import time as _time

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
            # 将所有仍在运行中的 ToolBlock 标记为中断，停止 spinner
            for block in self._assembler.tool_blocks.values():
                if block.status == ToolStatus.RUNNING:
                    block.set_interrupted()
            # 强制终止 AgentGroup（将未完成的 agent 标记为 interrupted）
            if self._assembler.agent_group is not None:
                self._assembler.agent_group.force_finalize()
            self._finalize_assistant_msg()
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint(
                "● ",
                "Interrupted",
                style=f"dim {get_color('error')}",
            )
            self._interrupted = True
            self._finish_run()
            return

        # IDLE 状态下双击 Esc → 打开 rewind 界面
        now = _time.monotonic()
        if now - self._last_esc < 0.5:
            self._last_esc = 0.0
            await self._open_rewind_screen()
            return
        self._last_esc = now

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
        """任意非 Ctrl+C/Escape 按键重置双击退出/rewind 窗口。"""
        if event.key != "ctrl+c" and event.key != "escape":
            if self._last_ctrl_c:
                self._last_ctrl_c = 0.0
                try:
                    self.query_one(InputBar).hide_exit_hint()
                except NoMatches:
                    pass
            if self._last_esc:
                self._last_esc = 0.0

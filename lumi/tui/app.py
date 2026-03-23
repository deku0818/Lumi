"""Lumi TUI 主应用"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Static

from lumi import __version__
from lumi.agents.cron.delivery import DeliveryManager, TUIDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.tui.agent_bridge import AgentBridge, BridgeEvent
from lumi.tui.run_state import RunContext, RunPhase
from lumi.tui.subagent_tracker import SubagentTracker
from lumi.tui.theme import APP_CSS, LUMI_DARK_THEME, LUMI_LIGHT_THEME, get_color
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.title_block import TitleBlock
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.input_bar import ChatInput, InputBar
from lumi.tui.widgets.command_result_panel import CommandResultPanel
from lumi.tui.widgets.run_status_bar import RunStatusBar
from lumi.tui.widgets.status_line import StatusLine
from lumi.tui.widgets.tool_block import ToolBlock, ToolStatus
from lumi.tui.widgets.user_message import UserMessage
from lumi.tui.screens.init_flow_screen import InitFlowScreen
from lumi.tui.screens.settings_screen import SettingsScreen
from lumi.utils.clipboard import copy_to_clipboard
from lumi.utils.config import GlobalConfig, GlobalConfigManager, get_config
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger
from lumi.utils.thread_id import generate_thread_id

from lumi.tui.event_router import EventRouter
from lumi.tui.message_restore import restore_messages
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.tui.slash_commands.registry import CommandRegistry
from lumi.tui.slash_commands.models import CommandType, SlashCommand
from lumi.tui.slash_commands.parser import parse_command_input
from lumi.tui.slash_commands.handlers import make_skill_handler
from lumi.agents.tools.skill_detector import SkillChangeDetector

from typing import Final

# 后台任务通知轮询间隔（秒）
_NOTIFICATION_POLL_INTERVAL: Final = 2.0


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
    ]

    def __init__(self) -> None:
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
        self._todos_all_done: bool = False  # todos 全完成，下次发消息时清除面板
        self._todos_hidden_for_approval: bool = False  # 审批期间临时隐藏 todos-bar

    def _query_safe(self, widget_type: type[Widget]) -> Widget | None:
        """按类型查询 widget，未挂载时返回 None 而非抛异常。"""
        try:
            return self.query_one(widget_type)
        except NoMatches:
            return None

    def _update_todos_bar(self, todos: list[dict]) -> None:
        """更新 #todos-bar 面板内容。

        无任务时隐藏；全部完成时仍然渲染（展示最终全勾状态），
        但标记 _todos_all_done，下次用户发消息时再清除面板。
        """
        from lumi.tui.renderers.todos import build_todos_text

        try:
            bar = self.query_one("#todos-bar", Static)
        except NoMatches:
            return
        if not todos:
            bar.update("")
            bar.remove_class("-visible")
            self._todos_all_done = False
            return
        bar.update(build_todos_text(todos))
        bar.add_class("-visible")
        self._todos_all_done = all(t.get("status") == "completed" for t in todos)

    def _clear_todos_bar(self) -> None:
        """隐藏并清空 #todos-bar 面板。"""
        self._todos_all_done = False
        try:
            bar = self.query_one("#todos-bar", Static)
        except NoMatches:
            return
        bar.update("")
        bar.remove_class("-visible")

    def _hide_todos_bar_for_approval(self) -> None:
        """审批期间临时隐藏 todos-bar，避免遮挡审批 UI。"""
        try:
            bar = self.query_one("#todos-bar", Static)
        except NoMatches:
            return
        self._todos_hidden_for_approval = bar.has_class("-visible")
        bar.remove_class("-visible")

    def _restore_todos_bar_after_approval(self) -> None:
        """审批结束后恢复 todos-bar 的可见状态。"""
        if not getattr(self, "_todos_hidden_for_approval", False):
            return
        self._todos_hidden_for_approval = False
        try:
            bar = self.query_one("#todos-bar", Static)
        except NoMatches:
            return
        # 隐藏前已记录可见状态，直接恢复
        bar.add_class("-visible")

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield RunStatusBar()
        yield Static("", id="todos-bar")
        yield CommandResultPanel()
        yield InputBar(id="input-area")
        yield StatusLine()

    async def _detect_system_theme(self) -> bool:
        """检测系统主题，返回 True 表示暗色。

        macOS: 通过 `defaults read -g AppleInterfaceStyle` 检测。
        Windows: 通过注册表 AppsUseLightTheme 键值检测。
        Linux 及其他平台: 默认暗色。
        """
        if sys.platform == "darwin":
            return await self._detect_macos_theme()
        if sys.platform == "win32":
            return self._detect_windows_theme()
        # Linux 及其他平台默认暗色
        return True

    async def _detect_macos_theme(self) -> bool:
        """macOS 暗色主题检测。"""
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

    @staticmethod
    def _detect_windows_theme() -> bool:
        """Windows 暗色主题检测，通过注册表读取 AppsUseLightTheme。"""
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            # 0 = 暗色, 1 = 亮色
            return value == 0
        except Exception:
            logger.debug("[LumiApp] Windows 注册表主题检测失败，使用暗色主题")
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
        sl = self._query_safe(StatusLine)
        if sl:
            sl.refresh_display()

    async def _finish_mount(self) -> None:
        """应用主题、注入环境变量并初始化 Agent bridge。"""
        # 初始化 WidgetAssembler（ChatLog 已在 compose 中创建）
        self._assembler = WidgetAssembler(self.query_one(ChatLog))
        # 绑定 RunContext 到 RunStatusBar，使 spinner tick 可读取实时状态
        self.query_one(RunStatusBar).bind_run_context(self._run)

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

        # 初始化 Shadow Git Checkpoint
        from pathlib import Path as _Path

        self._bridge.init_shadow_git(_Path.cwd())

        # 配置 StatusLine（尝试从 OpenRouter 获取 context_length）
        from lumi.utils.model_info import fetch_model_info
        from lumi.utils.read_config import get_config as get_yaml_config

        config_context_length = get_yaml_config().config.token.context_length
        model_name = self._bridge.model_name

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

        # TitleBlock 挂载到 ChatLog 内部，随聊天内容一起滚动
        chat_log = self.query_one(ChatLog)
        recent_sessions = await self._load_recent_sessions()
        await self._mount_title_block(chat_log, recent_sessions=recent_sessions)

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

        # 启动后台任务通知轮询
        self._notification_poll_timer = self.set_interval(
            _NOTIFICATION_POLL_INTERVAL, self._poll_notifications
        )

        # 绑定用户自定义快捷键
        self._bind_custom_keys(self._global_config)

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
            # 全部完成的任务不再展示，避免 resume 进已完成会话时残留面板
            all_done = all(t.get("status") == "completed" for t in todos)
            if not all_done:
                self._update_todos_bar(todos)
            else:
                self._clear_todos_bar()
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
            chat_log = self.query_one(ChatLog)
            await chat_log.append_hint("● ", "No checkpoints available")
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
        from lumi.agents.tools.config import load_agents

        agents = load_agents()

        from lumi.tui.screens.agents_screen import AgentsScreen

        await self._push_list_screen(
            agents, AgentsScreen, "暂无可用 Agent", self._on_agents_done
        )

    async def _on_agents_done(self, agent_name: str | None) -> None:
        """Agent 列表界面关闭回调。"""

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

        store = NotificationStore()
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

    # ── 输入处理 ──

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._run.is_running:
            return

        # 上一轮 todos 全部完成 → 清除面板
        if self._todos_all_done:
            self._clear_todos_bar()

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
        self._run.start()
        self.query_one(InputBar).set_disabled(True)

        self._run.task = asyncio.create_task(self._run_stream(content, tool_mode))

    async def _run_stream(
        self, content: str | list, tool_mode: str = "approve"
    ) -> None:
        """执行流 - 默认使用 approve 模式以保证安全

        Args:
            content: 用户输入内容
            tool_mode: 工具执行模式，默认为 "approve"（需要人工审批）
        """
        await self._consume_events(self._bridge.stream_response(content, tool_mode))

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

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        tool_call_id = (evt.data or {}).get("tool_call_id", "")
        key = tool_call_id or "ask"
        block = self._assembler.tool_blocks.get(key)
        # Fallback: TOOL_START 中 InjectedToolCallId 可能不在事件 input 中，
        # 导致 ToolBlock 以工具名 "ask" 为 key 存储
        if block is None:
            result = self._assembler.find_tool_block_by_name("ask")
            if result is not None:
                _, block = result
        if block:
            dialog = AskDialog(evt.data)
            await block.mount_interactive(dialog)

    async def _handle_tool_approval(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        self._assembler.finalize_assistant_msg()
        # 保存工具调用信息，供拒绝/取消时创建 ToolBlock
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
        await chat_log.mount(approval)

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
                    msg = (
                        "用户中断了审批"
                        if decision == "cancel"
                        else "用户拒绝了此工具执行"
                    )
                    block.set_error(msg)
            await chat_log.auto_scroll_if_needed()
        # 审批结束，恢复 todos-bar
        self._restore_todos_bar_after_approval()
        # 清理审批上下文
        self._subagent_tracker.clear_approval_context()
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
        # 隐藏运行状态栏
        bar = self._query_safe(RunStatusBar)
        if bar:
            bar.hide()
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
        self._run.task = asyncio.create_task(self._run_stream(hint, tool_mode="auto"))

    def action_scroll_chat(self, direction: str) -> None:
        """滚动聊天日志区域。

        Args:
            direction: 滚动方向 — "up" / "down" / "page_up" / "page_down"
        """
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
            self._global_config = result
            await self._apply_theme_mode(result.theme_mode)

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

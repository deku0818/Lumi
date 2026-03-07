"""Lumi TUI 主应用"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Key
from textual.widgets import Input, Static

from lumi import __version__
from lumi.agents.cron.delivery import DeliveryManager, TUIDelivery
from lumi.agents.cron.job_store import JobStore
from lumi.agents.cron.run_log import RunLog
from lumi.agents.cron.scheduler import Scheduler
from lumi.agents.tools.providers.cron import init_cron_tool
from lumi.tui.agent_bridge import AgentBridge, EventKind
from lumi.tui.theme import APP_CSS, LUMI_DARK_THEME, LUMI_LIGHT_THEME, get_color
from lumi.tui.widgets.ask_block import AskBlock
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.title_block import TitleBlock
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.input_bar import InputBar
from lumi.tui.widgets.notification_panel import NotificationChanged, NotificationPanel
from lumi.tui.widgets.thinking_indicator import ThinkingIndicator
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.user_message import UserMessage
from lumi.tui.screens.init_flow_screen import InitFlowScreen
from lumi.tui.screens.settings_screen import SettingsScreen
from lumi.utils.config import GlobalConfig, GlobalConfigManager, get_config
from lumi.utils.config.global_manager import GLOBAL_CONFIG_DIR
from lumi.utils.logger import logger


class LumiApp(App):
    """Lumi TUI 主应用"""

    CSS = APP_CSS
    TITLE = "Lumi"
    BINDINGS = [
        Binding("escape", "cancel_generation", "Cancel", priority=True),
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("ctrl+s", "open_settings", "Settings", priority=True),
        Binding("ctrl+n", "toggle_notifications", "Notifications", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(LUMI_DARK_THEME)
        self.register_theme(LUMI_LIGHT_THEME)
        self.theme = "lumi-dark"  # 默认暗色，on_mount 中根据全局配置切换
        self._bridge = AgentBridge()
        self._current_assistant_msg: AssistantMessage | None = None
        self._current_thinking: ThinkingIndicator | None = None
        self._agent_running = False
        self._tool_blocks: dict[str, ToolBlock] = {}
        self._last_approval_tool_calls: list[dict] = []
        self._current_ask_block: AskBlock | None = None
        self._current_task: asyncio.Task | None = None
        self._last_ctrl_c: float = 0.0
        self._global_config = None
        self._scheduler: Scheduler | None = None
        self._delivery: DeliveryManager | None = None

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield NotificationPanel()
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
            err_text = Text()
            err_text.append("✗ 初始化失败: ", style=f"bold {get_color('error')}")
            err_text.append(str(e), style=get_color("error"))
            await chat_log.mount(Static(err_text, markup=False))
            return

        # TitleBlock 挂载到 ChatLog 内部，随聊天内容一起滚动
        chat_log = self.query_one(ChatLog)
        title = TitleBlock(
            model_name=self._bridge.model_name,
            id="title-block",
        )
        title.border_title = f"Lumi v{__version__}"
        await chat_log.mount(title)

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

    # ── 输入处理 ──

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        if self._agent_running:
            return

        text = event.text
        tool_mode = event.tool_mode
        chat_log = self.query_one(ChatLog)
        await chat_log.mount(UserMessage(text))
        await chat_log.auto_scroll_if_needed()

        self._agent_running = True
        self.query_one(InputBar).set_disabled(True)

        self._current_task = asyncio.create_task(self._run_stream(text, tool_mode))

    async def _run_stream(self, text: str, tool_mode: str = "approve") -> None:
        await self._consume_events(self._bridge.stream_response(text, tool_mode))

    async def _run_resume(self, value) -> None:
        self._current_task = asyncio.create_task(
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

    async def _apply_event(self, evt, chat_log: ChatLog) -> None:
        """处理单个 bridge 事件并更新 UI"""
        match evt.kind:
            case EventKind.MODEL_START:
                await self._start_thinking(chat_log)
            case EventKind.STREAM_TOKEN:
                await self._handle_stream_token(evt, chat_log)
            case EventKind.MODEL_END:
                self._finalize_assistant_msg()
            case EventKind.TOOL_CALL_CHUNK:
                if not self._current_thinking:
                    self._finalize_assistant_msg()
                    await self._start_thinking(chat_log)
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

    async def _handle_stream_token(self, evt, chat_log: ChatLog) -> None:
        """处理流式 token 事件"""
        self._stop_thinking()
        if self._current_assistant_msg is None:
            self._current_assistant_msg = AssistantMessage()
            await chat_log.mount(self._current_assistant_msg)
        self._current_assistant_msg.append_token(evt.text)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_start(self, evt, chat_log: ChatLog) -> None:
        """处理工具开始执行事件"""
        self._stop_thinking()
        self._finalize_assistant_msg()
        # ask 工具由 AskBlock 统一处理，不创建 ToolBlock
        if evt.name == "ask":
            return
        key = evt.tool_call_id or evt.name
        # 审批模式下 ToolBlock 已在 TOOL_APPROVAL 阶段创建
        if key not in self._tool_blocks:
            block = ToolBlock(evt.name, evt.args or {}, approval_mode=evt.approval_mode)
            self._tool_blocks[key] = block
            await chat_log.mount(block)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_end(self, evt, chat_log: ChatLog) -> None:
        """处理工具执行完成事件"""
        self._stop_thinking()
        # ask 工具由 AskBlock 统一处理
        if evt.name == "ask":
            return
        key = evt.tool_call_id or evt.name
        block = self._tool_blocks.pop(key, None)
        if block:
            block.set_done(evt.output)
        await chat_log.auto_scroll_if_needed()

    async def _handle_ask(self, evt, chat_log: ChatLog) -> None:
        """处理 ask 中断事件"""
        self._stop_thinking()
        ask_block = AskBlock(evt.data)
        self._current_ask_block = ask_block
        await chat_log.mount(ask_block)
        await chat_log.auto_scroll_if_needed()

    async def _handle_tool_approval(self, evt, chat_log: ChatLog) -> None:
        """处理工具审批中断事件"""
        self._stop_thinking()
        self._finalize_assistant_msg()
        # 保存工具调用信息，供拒绝/取消时创建 ToolBlock
        tool_calls = (evt.data or {}).get("tool_calls", [])
        self._last_approval_tool_calls = tool_calls
        for tc in tool_calls:
            key = tc.get("id") or tc.get("name", "unknown")
            self._tool_blocks.pop(key, None)
        approval = ToolApproval(evt.data)
        await chat_log.mount(approval)
        await chat_log.auto_scroll_if_needed()

    def _stop_thinking(self) -> None:
        if self._current_thinking:
            self._current_thinking.stop()
            self._current_thinking = None
        # 清理可能残留在 DOM 中的 ThinkingIndicator
        try:
            for indicator in self.query(ThinkingIndicator):
                indicator.stop()
                indicator.remove()
        except Exception:
            pass

    def _finalize_assistant_msg(self) -> None:
        if self._current_assistant_msg:
            self._current_assistant_msg.finalize()
            self._current_assistant_msg = None

    async def _start_thinking(self, chat_log: ChatLog) -> None:
        self._stop_thinking()
        self._current_thinking = ThinkingIndicator()
        await chat_log.mount(self._current_thinking)
        await chat_log.auto_scroll_if_needed()

    async def _show_error(self, chat_log: ChatLog, error: str) -> None:
        self._stop_thinking()
        if len(error) > 300:
            error = error[:300] + "..."
        err_text = Text()
        err_text.append("✗ Error: ", style=f"bold {get_color('error')}")
        err_text.append(error, style=get_color("error"))
        await chat_log.mount(Static(err_text, markup=False))
        await chat_log.auto_scroll_if_needed()
        self._finish_run()

    # ── 中断恢复 ──

    async def on_ask_dialog_answered(self, event: AskDialog.Answered) -> None:
        if self._current_ask_block:
            self._current_ask_block.set_result(event.answer)
            self._current_ask_block = None
        await self._run_resume(event.answer)

    async def on_tool_approval_decided(self, event: ToolApproval.Decided) -> None:
        decision = event.decision
        # 拒绝或取消时，创建标记为错误的 ToolBlock 保留视觉记录
        if decision in ("reject", "cancel"):
            chat_log = self.query_one(ChatLog)
            tool_calls = getattr(event, "_tool_calls", None)
            # 从最近的 ToolApproval 数据中恢复工具信息
            if tool_calls is None:
                # ToolApproval 已被 remove，从 _last_approval_data 获取
                tool_calls = self._last_approval_tool_calls
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
            self.query_one(NotificationPanel).add_notification(
                job_name, output, started_at=started_at, duration_ms=duration_ms
            )
        except Exception:
            logger.warning("[LumiApp] 通知面板不可用", exc_info=True)

    def action_toggle_notifications(self) -> None:
        """切换通知面板显示/隐藏 (Ctrl+N)。"""
        try:
            self.query_one(NotificationPanel).toggle_panel()
        except NoMatches:
            logger.debug("[LumiApp] NotificationPanel 尚未挂载")
        except Exception:
            logger.warning("[LumiApp] 切换通知面板失败", exc_info=True)

    def on_notification_changed(self, event: NotificationChanged) -> None:
        """通知数量变化时更新 InputBar 铃铛。"""
        try:
            self.query_one(InputBar).update_bell(event.unread)
        except NoMatches:
            logger.debug("[LumiApp] InputBar 尚未挂载")
        except Exception:
            logger.warning(
                "[LumiApp] 铃铛更新失败, unread=%s", event.unread, exc_info=True
            )

    def _finish_run(self) -> None:
        self._agent_running = False
        self._current_assistant_msg = None
        self._current_ask_block = None
        self._stop_thinking()
        self._tool_blocks.clear()
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
        # 如果当前有审批组件，esc 触发审批中断而非取消生成
        try:
            approval = self.query_one(ToolApproval)
            approval.post_message(ToolApproval.Decided("cancel"))
            approval.call_later(approval.remove)
            return
        except NoMatches:
            pass

        if self._agent_running:
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
            self._stop_thinking()
            self._finalize_assistant_msg()
            chat_log = self.query_one(ChatLog)
            hint = Text()
            hint.append("⏹ ", style="dim")
            hint.append("已中断生成", style=f"dim {get_color('warning')}")
            await chat_log.mount(Static(hint, markup=False))
            await chat_log.auto_scroll_if_needed()
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
            inp = self.query_one("#user-input", Input)
        except Exception:
            await self.action_quit_app()
            return

        # 输入框有内容 → 清空，重置退出计时
        if inp.value:
            inp.value = ""
            self._last_ctrl_c = 0.0
            return

        # 输入框已空 → 判断是否双击退出
        now = time.monotonic()
        if now - self._last_ctrl_c < 1.5:
            await self.action_quit_app()
            return
        self._last_ctrl_c = now

    def on_key(self, event: Key) -> None:
        """任意非 Ctrl+C 按键重置双击退出窗口。"""
        if event.key != "ctrl+c":
            self._last_ctrl_c = 0.0

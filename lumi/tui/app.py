"""Lumi TUI 主应用"""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Static

from lumi import __version__
from lumi.tui.agent_bridge import AgentBridge, EventKind
from lumi.tui.theme import APP_CSS
from lumi.tui.widgets.ask_dialog import AskDialog
from lumi.tui.widgets.tool_approval import ToolApproval
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.title_block import TitleBlock
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.input_bar import InputBar
from lumi.tui.widgets.thinking_indicator import ThinkingIndicator
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.user_message import UserMessage
from lumi.utils.logger import logger


class LumiApp(App):
    """Lumi TUI 主应用"""

    CSS = APP_CSS
    TITLE = "Lumi"
    BINDINGS = [
        ("escape", "cancel_generation", "Cancel"),
        ("ctrl+c", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._bridge = AgentBridge()
        self._current_assistant_msg: AssistantMessage | None = None
        self._current_thinking: ThinkingIndicator | None = None
        self._agent_running = False
        self._tool_blocks: dict[str, ToolBlock] = {}

    def compose(self) -> ComposeResult:
        yield ChatLog()
        yield InputBar(id="input-area")

    async def on_mount(self) -> None:
        try:
            await self._bridge.initialize()
        except Exception as e:
            chat_log = self.query_one(ChatLog)
            err_text = Text()
            err_text.append("✗ 初始化失败: ", style="bold #ef5350")
            err_text.append(str(e), style="#ef5350")
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

        await self._run_stream(text, tool_mode)

    async def _run_stream(self, text: str, tool_mode: str = "approve") -> None:
        await self._consume_events(self._bridge.stream_response(text, tool_mode))

    async def _run_resume(self, value) -> None:
        await self._consume_events(self._bridge.stream_resume(value))

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
                self._current_thinking = ThinkingIndicator()
                await chat_log.mount(self._current_thinking)
                await chat_log.auto_scroll_if_needed()

            case EventKind.STREAM_TOKEN:
                self._stop_thinking()
                if self._current_assistant_msg is None:
                    self._current_assistant_msg = AssistantMessage()
                    await chat_log.mount(self._current_assistant_msg)
                self._current_assistant_msg.append_token(evt.text)
                await chat_log.auto_scroll_if_needed()

            case EventKind.MODEL_END:
                self._stop_thinking()
                if self._current_assistant_msg:
                    self._current_assistant_msg.finalize()
                    self._current_assistant_msg = None

            case EventKind.TOOL_START:
                if self._current_assistant_msg:
                    self._current_assistant_msg.finalize()
                    self._current_assistant_msg = None
                block = ToolBlock(evt.name, evt.args or {})
                self._tool_blocks[evt.tool_call_id or evt.name] = block
                await chat_log.mount(block)
                await chat_log.auto_scroll_if_needed()

            case EventKind.TOOL_END:
                key = evt.tool_call_id or evt.name
                block = self._tool_blocks.pop(key, None)
                if block:
                    block.set_done(evt.output)
                await chat_log.auto_scroll_if_needed()

            case EventKind.ASK:
                dialog = AskDialog(evt.data)
                await chat_log.mount(dialog)
                await chat_log.auto_scroll_if_needed()

            case EventKind.TOOL_APPROVAL:
                approval = ToolApproval(evt.data)
                await chat_log.mount(approval)
                await chat_log.auto_scroll_if_needed()

            case EventKind.DONE:
                self._finish_run()

            case EventKind.ERROR:
                await self._show_error(chat_log, evt.error)

    def _stop_thinking(self) -> None:
        if self._current_thinking:
            self._current_thinking.stop()
            self._current_thinking = None

    async def _show_error(self, chat_log: ChatLog, error: str) -> None:
        self._stop_thinking()
        if len(error) > 300:
            error = error[:300] + "..."
        err_text = Text()
        err_text.append("✗ Error: ", style="bold #ef5350")
        err_text.append(error, style="#ef5350")
        await chat_log.mount(Static(err_text, markup=False))
        await chat_log.auto_scroll_if_needed()
        self._finish_run()

    # ── 中断恢复 ──

    async def on_ask_dialog_answered(self, event: AskDialog.Answered) -> None:
        await self._run_resume(event.answer)

    async def on_tool_approval_decided(self, event: ToolApproval.Decided) -> None:
        if event.decision == "auto":
            self.query_one(InputBar).set_tool_mode("auto")
        await self._run_resume(event.decision)

    # ── 操作 ──

    def _finish_run(self) -> None:
        self._agent_running = False
        self._current_assistant_msg = None
        self._stop_thinking()
        self._tool_blocks.clear()
        try:
            self.query_one(InputBar).set_disabled(False)
        except Exception:
            pass

    async def action_cancel_generation(self) -> None:
        if self._agent_running:
            self._finish_run()

    async def action_quit_app(self) -> None:
        await self._bridge.close()
        self.exit()

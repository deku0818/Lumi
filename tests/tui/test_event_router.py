"""EventRouter 状态机转换测试

覆盖场景：
1. 交替的 STREAM_TOKEN + TOOL_CALL_CHUNK 不拆分消息
2. TOOL_START 正确终结流式消息
3. MODEL_END 正确终结流式消息
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import App, ComposeResult

from lumi.agents.bridge import BridgeEvent, EventKind
from lumi.tui.event_router import EventRouter
from lumi.tui.run_state import RunContext, RunPhase
from lumi.tui.subagent_tracker import SubagentTracker
from lumi.tui.theme import LUMI_DARK_THEME, LUMI_LIGHT_THEME
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.tui.widgets.assistant_message import AssistantMessage
from lumi.tui.widgets.chat_log import ChatLog


@dataclass
class _FakeCallbacks:
    _finished: bool = False

    async def _handle_ask(self, evt, chat_log) -> None:
        pass

    async def _handle_tool_approval(self, evt, chat_log) -> None:
        pass

    async def _handle_exit_plan_mode(self, evt, chat_log) -> None:
        pass

    def _sync_plan_mode_from_tool(self) -> None:
        pass

    async def _show_error(self, chat_log, error) -> None:
        pass

    def _finish_run(self) -> None:
        self._finished = True

    def _query_safe(self, widget_type):
        return None

    def _update_todos_bar(self, todos) -> None:
        pass


class _TestApp(App):
    CSS = """
    Screen { layout: vertical; }
    ChatLog { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(LUMI_DARK_THEME)
        self.register_theme(LUMI_LIGHT_THEME)
        self.theme = "lumi-dark"

    def compose(self) -> ComposeResult:
        yield ChatLog()


def _evt(kind: EventKind, **kwargs) -> BridgeEvent:
    return BridgeEvent(kind=kind, **kwargs)


# ── 1. 交替的 STREAM_TOKEN + TOOL_CALL_CHUNK 不拆分消息 ──


async def test_interleaved_tool_call_chunks_no_split():
    """TOOL_CALL_CHUNK 交替出现时不应终结当前 AssistantMessage。"""
    app = _TestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        asm = WidgetAssembler(chat_log)
        tracker = SubagentTracker()
        router = EventRouter(run, asm, tracker, _FakeCallbacks())

        # 模拟交替的 text 和 tool_call_chunk
        await router.dispatch(_evt(EventKind.MODEL_START), chat_log)
        await router.dispatch(_evt(EventKind.STREAM_TOKEN, text="Hello "), chat_log)
        await router.dispatch(_evt(EventKind.TOOL_CALL_CHUNK), chat_log)
        await router.dispatch(_evt(EventKind.STREAM_TOKEN, text="world"), chat_log)
        await router.dispatch(_evt(EventKind.TOOL_CALL_CHUNK), chat_log)

        # 应该只有一个 AssistantMessage
        msgs = chat_log.query(AssistantMessage)
        assert len(msgs) == 1
        assert "Hello " in msgs.first()._raw
        assert "world" in msgs.first()._raw


# ── 2. TOOL_START 正确终结流式消息 ──


async def test_tool_start_finalizes_message():
    """TOOL_START 应终结当前流式 AssistantMessage。"""
    app = _TestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        asm = WidgetAssembler(chat_log)
        tracker = SubagentTracker()
        router = EventRouter(run, asm, tracker, _FakeCallbacks())

        await router.dispatch(_evt(EventKind.MODEL_START), chat_log)
        await router.dispatch(
            _evt(EventKind.STREAM_TOKEN, text="Before tool"), chat_log
        )
        await router.dispatch(
            _evt(EventKind.TOOL_START, name="bash", args={"command": "ls"}),
            chat_log,
        )

        # 消息应已终结
        assert asm.assistant_msg is None
        # phase 应为 TOOL_RUNNING
        assert run.phase == RunPhase.TOOL_RUNNING


# ── 3. MODEL_END 正确终结流式消息 ──


async def test_model_end_finalizes_message():
    """MODEL_END 应终结当前流式 AssistantMessage。"""
    app = _TestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        asm = WidgetAssembler(chat_log)
        tracker = SubagentTracker()
        router = EventRouter(run, asm, tracker, _FakeCallbacks())

        await router.dispatch(_evt(EventKind.MODEL_START), chat_log)
        await router.dispatch(_evt(EventKind.STREAM_TOKEN, text="Complete"), chat_log)
        await router.dispatch(_evt(EventKind.MODEL_END), chat_log)

        # 消息应已终结
        assert asm.assistant_msg is None
        msgs = chat_log.query(AssistantMessage)
        assert len(msgs) == 1


# ── 4. 完整流程：文本 → tool_call_chunk → tool → 文本 ──


async def test_full_flow_text_tool_text():
    """完整流程：文本流 + 工具调用 + 后续文本应产生两个独立消息。"""
    app = _TestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        asm = WidgetAssembler(chat_log)
        tracker = SubagentTracker()
        router = EventRouter(run, asm, tracker, _FakeCallbacks())

        # 第一轮：文本 + tool_call_chunk + tool
        await router.dispatch(_evt(EventKind.MODEL_START), chat_log)
        await router.dispatch(_evt(EventKind.STREAM_TOKEN, text="Part 1"), chat_log)
        await router.dispatch(_evt(EventKind.TOOL_CALL_CHUNK), chat_log)
        await router.dispatch(_evt(EventKind.MODEL_END), chat_log)
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="bash",
                args={"command": "ls"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END, name="bash", output="file.txt", tool_call_id="tc1"
            ),
            chat_log,
        )

        # 第二轮：更多文本
        await router.dispatch(_evt(EventKind.MODEL_START), chat_log)
        await router.dispatch(_evt(EventKind.STREAM_TOKEN, text="Part 2"), chat_log)
        await router.dispatch(_evt(EventKind.MODEL_END), chat_log)

        # 应有两个 AssistantMessage（工具前后各一个）
        msgs = chat_log.query(AssistantMessage)
        assert len(msgs) == 2

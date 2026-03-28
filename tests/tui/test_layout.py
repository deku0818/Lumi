"""TUI 布局与交互测试

覆盖以下场景：
1. 单个 ToolBlock 不被 ToolGroup 包裹，直接挂载到 ChatLog
2. 多个连续 ToolBlock 合并为 ToolGroup
3. AgentGroup 渲染：标题行 + agent 行，点击展开详情
4. AgentGroup 跨 resume 持久化（RunContext.agent_group）
5. 子代理审批取消时不创建多余 ToolBlock
6. ChatLog 滚动行为：向上滚动禁用自动滚动，到底部恢复
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import App, ComposeResult

from lumi.tui.agent_bridge import BridgeEvent, EventKind
from lumi.tui.event_router import EventRouter
from lumi.tui.run_state import RunContext
from lumi.tui.subagent_tracker import SubagentTracker
from lumi.tui.theme import LUMI_DARK_THEME, LUMI_LIGHT_THEME
from lumi.tui.widget_assembler import WidgetAssembler
from lumi.tui.widgets.agent_group import AgentGroup
from lumi.tui.widgets.chat_log import ChatLog
from lumi.tui.widgets.tool_block import ToolBlock
from lumi.tui.widgets.tool_group import ToolGroup


# ── 测试用 App ──


@dataclass
class _FakeCallbacks:
    """EventRouter 回调的最小实现，不依赖真实 LumiApp。"""

    _finished: bool = False

    async def _handle_ask(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        pass

    async def _handle_tool_approval(self, evt: BridgeEvent, chat_log: ChatLog) -> None:
        pass

    async def _show_error(self, chat_log: ChatLog, error: str) -> None:
        pass

    def _finish_run(self) -> None:
        self._finished = True

    def _query_safe(self, widget_type: type) -> None:
        return None


class LayoutTestApp(App):
    """测试用 App，仅包含 ChatLog，注册 Lumi 主题以支持自定义 CSS 变量。"""

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


def _evt(
    kind: EventKind,
    *,
    name: str = "",
    args: dict | None = None,
    tool_call_id: str = "",
    output: str = "",
    run_id: str = "",
    parent_run_id: str = "",
    text: str = "",
) -> BridgeEvent:
    """构造 BridgeEvent 的便捷工厂。"""
    return BridgeEvent(
        kind=kind,
        name=name,
        args=args,
        tool_call_id=tool_call_id,
        output=output,
        run_id=run_id,
        parent_run_id=parent_run_id,
        text=text,
    )


# ── 1. 单个 ToolBlock 不被 ToolGroup 包裹 ──


async def test_single_tool_block_no_group():
    """单个工具调用应直接挂载到 ChatLog，不创建 ToolGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 发送一个 read 工具的 TOOL_START + TOOL_END
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "foo.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="read",
                tool_call_id="tc1",
                output="file content",
            ),
            chat_log,
        )
        # 触发 finalize（模拟流结束）
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        # ChatLog 中应有 ToolBlock，但没有 ToolGroup
        tool_blocks = chat_log.query(ToolBlock)
        tool_groups = chat_log.query(ToolGroup)
        assert len(tool_blocks) == 1, f"期望 1 个 ToolBlock，实际 {len(tool_blocks)}"
        assert len(tool_groups) == 0, "单个工具不应创建 ToolGroup"

        # ToolBlock 应是 ChatLog 的直接子节点
        block = tool_blocks.first()
        assert block.parent is chat_log


async def test_single_tool_block_padding():
    """单个 ToolBlock 直接挂载时应有 padding: 0 1。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="bash",
                args={"command": "ls"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        block = chat_log.query(ToolBlock).first()
        # ToolBlock DEFAULT_CSS 定义 padding: 0 1
        assert block.styles.padding.right == 1
        assert block.styles.padding.left == 1


# ── 2. 多个连续 ToolBlock 合并为 ToolGroup ──


async def test_multiple_tools_create_group():
    """两个连续工具调用应合并为一个 ToolGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 第一个工具
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "a.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        # 第二个工具（触发 ToolGroup 创建）
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "b.py"},
                tool_call_id="tc2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc1", output="a"),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc2", output="b"),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(ToolGroup)
        assert len(groups) == 1, f"期望 1 个 ToolGroup，实际 {len(groups)}"
        assert groups.first().block_count == 2


async def test_tool_group_summary_updates():
    """ToolGroup 完成后摘要应从 running 变为 finalized。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "a.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "b.py"},
                tool_call_id="tc2",
            ),
            chat_log,
        )
        await pilot.pause()

        group = chat_log.query(ToolGroup).first()
        assert not group.is_finalized

        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc1", output="a"),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc2", output="b"),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        assert group.is_finalized


async def test_text_between_tools_splits_groups():
    """工具之间有文本输出时应拆分为独立的 block/group。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 第一个工具
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "a.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc1", output="a"),
            chat_log,
        )
        # 中间有文本
        await router.dispatch(
            _evt(EventKind.STREAM_TOKEN, text="hello"),
            chat_log,
        )
        # 第二个工具
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "b.py"},
                tool_call_id="tc2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc2", output="b"),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        # 两个工具被文本打断，不应合并
        groups = chat_log.query(ToolGroup)
        assert len(groups) == 0, "被文本打断的工具不应合并为 ToolGroup"
        blocks = chat_log.query(ToolBlock)
        assert len(blocks) == 2


# ── 3. AgentGroup 渲染 ──


async def test_agent_group_creation():
    """agent 工具应创建 AgentGroup 并注册 agent 行。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "write code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await pilot.pause()

        assert asm.agent_group is not None
        groups = chat_log.query(AgentGroup)
        assert len(groups) == 1

        ag: AgentGroup = groups.first()
        entry = ag.get_entry("run-1")
        assert entry is not None
        assert entry.name == "coder"
        assert entry.prompt == "write code"


async def test_agent_group_finish():
    """agent 完成后 AgentGroup 应标记为 finalized。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "write code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END, name="agent", run_id="run-1", output="done coding"
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        assert ag.is_finalized
        entry = ag.get_entry("run-1")
        assert entry.done is True
        assert entry.result == "done coding"


async def test_agent_group_error():
    """agent 出错时 AgentGroup 应标记 error。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "write code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="用户中断了工具调用请求",
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        entry = ag.get_entry("run-1")
        assert entry.done is True
        assert entry.error is True


async def test_agent_group_multiple_agents():
    """多个 agent 应共享同一个 AgentGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 1, "多个 agent 应共享同一个 AgentGroup"
        ag: AgentGroup = groups.first()
        assert ag.get_entry("run-1") is not None
        assert ag.get_entry("run-2") is not None


async def test_subagent_events_update_stats():
    """子代理事件应更新 AgentGroup 统计而非创建 DOM。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 创建 agent
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await pilot.pause()

        # 子代理工具事件（parent_run_id 指向 agent）
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="bash",
                args={"command": "ls"},
                parent_run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="bash", parent_run_id="run-1"),
            chat_log,
        )
        await pilot.pause()

        # 子代理事件不应在 ChatLog 中创建 ToolBlock
        tool_blocks_in_chatlog = [
            w for w in chat_log.children if isinstance(w, ToolBlock)
        ]
        assert len(tool_blocks_in_chatlog) == 0, (
            "子代理事件不应在 ChatLog 创建 ToolBlock"
        )

        # AgentGroup 统计应更新
        ag: AgentGroup = chat_log.query(AgentGroup).first()
        entry = ag.get_entry("run-1")
        assert entry.tool_uses == 1


# ── 4. AgentGroup 跨 resume 持久化 ──


async def test_agent_group_persists_across_resume():
    """AgentGroup 存储在 WidgetAssembler 中，跨 EventRouter 实例持久化。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        asm = WidgetAssembler(chat_log)
        tracker = SubagentTracker()
        cb = _FakeCallbacks()

        # 第一个 EventRouter 创建 AgentGroup
        router1 = EventRouter(run, asm, tracker, cb)
        await router1.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await pilot.pause()

        ag_ref = asm.agent_group
        assert ag_ref is not None

        # 模拟 resume：创建新的 EventRouter，共享同一个 WidgetAssembler
        router2 = EventRouter(run, asm, tracker, cb)

        # 新 router 应能通过 WidgetAssembler 访问同一个 AgentGroup
        assert asm.agent_group is ag_ref

        # 新 agent 事件应加入已有的 AgentGroup
        await router2.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await pilot.pause()

        # 仍然只有一个 AgentGroup
        groups = chat_log.query(AgentGroup)
        assert len(groups) == 1
        assert asm.agent_group is ag_ref


# ── 5. 子代理审批取消不创建多余 ToolBlock ──


async def test_agent_cancel_no_stray_toolblock():
    """AgentGroup 模式下，子代理审批取消不应在 ChatLog 创建 ToolBlock。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 创建 agent
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await pilot.pause()

        # agent 被取消
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="用户拒绝了工具执行",
            ),
            chat_log,
        )
        await pilot.pause()

        # ChatLog 中不应有独立的 ToolBlock（错误信息在 AgentGroup 内展示）
        standalone_blocks = [w for w in chat_log.children if isinstance(w, ToolBlock)]
        assert len(standalone_blocks) == 0, (
            "AgentGroup 模式下审批取消不应在 ChatLog 创建独立 ToolBlock"
        )

        # AgentGroup 应标记 error
        ag: AgentGroup = chat_log.query(AgentGroup).first()
        entry = ag.get_entry("run-1")
        assert entry.error is True


# ── 6. ChatLog 滚动行为 ──


async def test_chatlog_auto_scroll_default():
    """ChatLog 默认启用自动滚动。"""
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        assert chat_log._auto_scroll is True


async def test_chatlog_scroll_up_disables_auto():
    """向上滚动应禁用自动滚动。

    需要 max_scroll_y 足够大，否则 watch_scroll_y 认为已在底部。
    通过 monkey-patch max_scroll_y 属性模拟有大量内容的场景。
    """
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        chat_log._auto_scroll = True
        # 让 max_scroll_y 返回一个大值，模拟有大量内容
        type(chat_log).max_scroll_y = property(lambda self: 500.0)
        try:
            chat_log.watch_scroll_y(100.0, 50.0)
            assert chat_log._auto_scroll is False
        finally:
            # 恢复原始属性
            del type(chat_log).max_scroll_y


async def test_chatlog_scroll_to_bottom_enables_auto():
    """滚动到底部应恢复自动滚动。"""
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        chat_log._auto_scroll = False
        # max_scroll_y 默认为 0，new=0 满足 at_bottom 条件
        chat_log.watch_scroll_y(10.0, 0.0)
        assert chat_log._auto_scroll is True


async def test_chatlog_scroll_pending_ignores_watch():
    """_scroll_pending 为 True 时 watch_scroll_y 不改变 _auto_scroll。"""
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        chat_log._auto_scroll = True
        chat_log._scroll_pending = True
        # 即使向上滚动，也不应禁用 auto_scroll
        chat_log.watch_scroll_y(100.0, 50.0)
        assert chat_log._auto_scroll is True


async def test_chatlog_scroll_to_end_sets_pending():
    """scroll_to_end 应设置 _scroll_pending 防止 watch 误判。"""
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        chat_log._auto_scroll = False
        await chat_log.scroll_to_end()
        assert chat_log._auto_scroll is True
        assert chat_log._scroll_pending is True


# ── 7. ToolGroup 折叠/展开 ──


async def test_tool_group_toggle():
    """ToolGroup 点击摘要行应切换展开/折叠。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "a.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "b.py"},
                tool_call_id="tc2",
            ),
            chat_log,
        )
        await pilot.pause()

        group = chat_log.query(ToolGroup).first()
        assert not group._expanded

        group.toggle_expanded()
        assert group._expanded

        group.toggle_expanded()
        assert not group._expanded


# ── 8. 两个工具可合并到同一个 ToolGroup ──


async def test_two_tools_merged_into_group():
    """两个连续的非 BYPASS 工具应被合并到同一个 ToolGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "a.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "b.py"},
                tool_call_id="tc2",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        blocks = chat_log.query(ToolBlock)
        assert len(blocks) == 2


# ── 9. RunContext 重置 ──


async def test_assembler_reset_clears_groups():
    """WidgetAssembler.reset() 应清除 agent_group 和 active_group 引用。"""
    app = LayoutTestApp()
    async with app.run_test():
        chat_log = app.query_one(ChatLog)
        asm = WidgetAssembler(chat_log)
        # 手动设置内部状态模拟运行中
        asm._agent_group = AgentGroup()
        asm._active_group = ToolGroup()
        asm.reset()
        assert asm.agent_group is None
        assert asm.active_group is None


# ── 10. AgentGroup detail 展开/折叠 ──


async def test_agent_detail_toggle():
    """完成的 agent 行点击应展开/折叠 prompt + result 详情。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "write tests"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END, name="agent", run_id="run-1", output="tests written"
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        entry = ag.get_entry("run-1")
        assert not entry.expanded

        # 展开
        ag.toggle_agent_detail("run-1")
        await pilot.pause()
        assert entry.expanded

        # 折叠
        ag.toggle_agent_detail("run-1")
        await pilot.pause()
        assert not entry.expanded


async def test_agent_detail_not_toggleable_while_running():
    """运行中的 agent 不应可展开详情。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        entry = ag.get_entry("run-1")
        assert not entry.done

        # 尝试展开 — 应无效
        ag.toggle_agent_detail("run-1")
        assert not entry.expanded


# ── 11. AgentGroup 中断后 force_finalize ──


async def test_agent_group_force_finalize_on_interrupt():
    """中断时 force_finalize 应停止 spinner 并标记未完成 agent 为 error。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 启动两个 agent，只完成一个
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done coding",
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        # run-2 还在运行，AgentGroup 未 finalized
        assert not ag.is_finalized
        assert ag._spinner_timer is not None

        # 模拟中断：force_finalize
        ag.force_finalize()
        await pilot.pause()

        assert ag.is_finalized
        assert ag._spinner_timer is None

        # run-1 正常完成，不受影响
        e1 = ag.get_entry("run-1")
        assert e1.done and not e1.error

        # run-2 被标记为 interrupted error
        e2 = ag.get_entry("run-2")
        assert e2.done and e2.error
        assert e2.current_action == "Interrupted"


async def test_agent_group_force_finalize_idempotent():
    """已 finalized 的 AgentGroup 再次 force_finalize 应无副作用。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done",
            ),
            chat_log,
        )
        await pilot.pause()

        ag: AgentGroup = chat_log.query(AgentGroup).first()
        assert ag.is_finalized

        # 再次 force_finalize 不应报错
        ag.force_finalize()
        assert ag.is_finalized
        e1 = ag.get_entry("run-1")
        assert e1.done and not e1.error


# ── 12. AgentGroup 分组分离 ──


async def test_agent_groups_split_by_text():
    """被文本输出分隔的 agent 调用应创建独立的 AgentGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 第一个 agent — 启动并完成
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done coding",
            ),
            chat_log,
        )

        # 中间有文本输出
        await router.dispatch(
            _evt(EventKind.STREAM_TOKEN, text="Now let me review..."),
            chat_log,
        )

        # 第二个 agent — 应创建新的 AgentGroup
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-2",
                output="looks good",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 2, f"期望 2 个 AgentGroup，实际 {len(groups)}"


async def test_parallel_agents_share_group():
    """并行 agent 调用（未完成时又来新的）应共享同一个 AgentGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 两个 agent 并行启动
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )

        # agent 运行中有文本到来 — 不应分离未完成的 AgentGroup
        await router.dispatch(
            _evt(EventKind.STREAM_TOKEN, text="waiting..."),
            chat_log,
        )

        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-2",
                output="done",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 1, "并行 agent 应共享同一个 AgentGroup"


async def test_agent_groups_split_by_normal_tool():
    """被普通工具调用分隔的 agent 调用应创建独立的 AgentGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 第一个 agent
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done",
            ),
            chat_log,
        )

        # 中间有普通工具调用
        await router.dispatch(
            _evt(EventKind.STREAM_TOKEN, text="Let me check..."),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="read",
                args={"path": "foo.py"},
                tool_call_id="tc1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(EventKind.TOOL_END, name="read", tool_call_id="tc1", output="content"),
            chat_log,
        )

        # 第二个 agent — 应创建新的 AgentGroup
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-2",
                output="looks good",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 2, f"期望 2 个 AgentGroup，实际 {len(groups)}"


# ── 13. AgentGroup 硬边界与兜底分离 ──


async def test_agent_group_split_by_flush_all():
    """flush_all（硬边界）应强制分离仍在运行的 AgentGroup。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # agent 启动但未完成
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )

        # 硬边界：模拟用户消息触发 flush_all
        await asm.flush_all()

        # 第二个 agent — 应创建新的 AgentGroup
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-2",
                output="done",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 2, f"期望 2 个 AgentGroup，实际 {len(groups)}"
        # 第一个组应被 force_finalize
        assert groups[0].is_finalized


async def test_agent_group_defensive_detach_back_to_back():
    """连续两组 agent 调用之间无文本时，_apply_agent_start 兜底逻辑应分离已完成的组。"""
    app = LayoutTestApp()
    async with app.run_test() as pilot:
        chat_log = app.query_one(ChatLog)
        run = RunContext()
        tracker = SubagentTracker()
        cb = _FakeCallbacks()
        asm = WidgetAssembler(chat_log)
        router = EventRouter(run, asm, tracker, cb)

        # 第一个 agent — 启动并完成
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "coder", "prompt": "code"},
                run_id="run-1",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-1",
                output="done",
            ),
            chat_log,
        )

        # 无文本/工具，直接第二个 agent — 触发兜底分离
        await router.dispatch(
            _evt(
                EventKind.TOOL_START,
                name="agent",
                args={"name": "reviewer", "prompt": "review"},
                run_id="run-2",
            ),
            chat_log,
        )
        await router.dispatch(
            _evt(
                EventKind.TOOL_END,
                name="agent",
                run_id="run-2",
                output="looks good",
            ),
            chat_log,
        )
        await router.dispatch(_evt(EventKind.DONE), chat_log)
        await pilot.pause()

        groups = chat_log.query(AgentGroup)
        assert len(groups) == 2, f"期望 2 个 AgentGroup，实际 {len(groups)}"

"""工具调用块组件 - 可折叠，显示运行状态，集成渲染器"""

from __future__ import annotations

from enum import StrEnum

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.utils import BLINK_FRAMES, SpinnerMixin
from lumi.tui.theme import get_color
from lumi.utils.logger import logger


class ToolStatus(StrEnum):
    """工具执行状态"""

    RUNNING = "running"
    WAITING = "waiting"  # 等待用户交互（ask dialog）
    DONE = "done"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class ToolBlock(Vertical, SpinnerMixin):
    """工具调用块 - 可折叠，显示工具名称、参数和输出

    通过 ToolDisplayRegistry 获取对应渲染器，生成专属的标题、参数和输出展示。
    审批模式下自动展开详细内容，便于用户审阅。
    运行中时标题行显示 spinner 动画。
    agent 工具类型额外提供子代理日志容器，用于嵌套展示子代理的执行过程。
    """

    DEFAULT_CSS = """
    ToolBlock {
        margin: 0 0 1 0;
        padding: 0 1;
        height: auto;
    }

    ToolBlock Collapsible {
        background: transparent;
        border: none;
        border-top: none;
        padding: 0;
        margin: 0;
    }

    ToolBlock CollapsibleTitle {
        padding: 0;
        margin: 0;
        color: $foreground;
        text-style: none;
    }

    ToolBlock CollapsibleTitle:focus {
        text-style: none;
        background: transparent;
    }

    ToolBlock Contents {
        padding: 0 0 0 3;
        margin: 0;
    }

    ToolBlock .tool-summary {
        padding: 0;
        color: $text-muted;
    }

    ToolBlock .tool-args {
        padding: 0 1;
        color: $text-muted;
    }

    ToolBlock .tool-output {
        padding: 0 1;
        color: $text-muted;
    }

    ToolBlock .subagent-log {
        padding: 0 0 0 3;
        margin: 0;
        height: auto;
    }

    ToolBlock .subagent-log ToolBlock {
        margin: 0 0 0 0;
    }
    """

    def __init__(
        self,
        name: str,
        args: dict,
        approval_mode: bool = False,
    ) -> None:
        super().__init__(classes="tool-block")
        self._name = name
        self._args = args
        self._approval_mode = approval_mode
        self._status = ToolStatus.RUNNING
        self._interactive: Widget | None = None
        self._is_agent = name == "agent"
        self._error_text: str = ""
        self._output_text: str = ""

        self._renderer = get_renderer(name)

        # 预计算标题文本（不随 spinner 变化，避免每帧重复调用 render_title）
        self._title_text = self._renderer.render_title(name, args)

    def compose(self) -> ComposeResult:
        """组合子组件：标题 + 参数区域 + (子代理日志) + 输出区域"""
        title_markup = self._build_title_markup()
        collapsed = not self._approval_mode  # 审批模式自动展开

        # 获取参数区域 Widget（_SafeRenderer 已处理异常回退）
        args_widget = self._renderer.render_args(
            self._args, approval_mode=self._approval_mode
        )

        args_widget.add_class("tool-args")
        with Collapsible(
            title=title_markup,
            collapsed=collapsed,
            collapsed_symbol="",
            expanded_symbol="",
            id=f"tool-{id(self)}",
        ):
            yield args_widget
            # agent 工具额外提供子代理日志容器
            if self._is_agent:
                yield Vertical(
                    classes="subagent-log",
                    id=f"subagent-log-{id(self)}",
                )

    def on_mount(self) -> None:
        """挂载后刷新标题颜色并启动闪烁动画。

        若 block 在 pending 阶段已被标记为 DONE/ERROR（set_done/set_error
        在挂载前调用），此时补挂摘要行并更新标题和折叠状态。
        """
        # 覆盖 CollapsibleTitle._update_label，避免空 symbol 拼出前导空格
        collapsible = self.query_one(Collapsible)
        title_widget = collapsible._title
        original_update_label = title_widget._update_label

        def _patched_update_label() -> None:
            original_update_label()
            # 立即用我们的版本覆盖（去掉前导空格）
            self._update_title_label()

        title_widget._update_label = _patched_update_label
        self._update_title_label()

        if self._status in (ToolStatus.DONE, ToolStatus.ERROR, ToolStatus.INTERRUPTED):
            # pending 阶段已完成，补挂摘要行并折叠
            self._try_mount_summary()
            collapsible.collapsed = True
        elif self._status == ToolStatus.RUNNING:
            self._start_spinner(interval=0.5)

    def _on_spinner_tick(self, frame_char: str) -> None:
        """SpinnerMixin 回调：更新标题行 spinner"""
        if self._status != ToolStatus.RUNNING:
            return
        try:
            self._update_title_label()
        except NoMatches:
            pass  # 尚未挂载，下次再试
        except Exception:
            logger.warning(
                "spinner 更新失败，停止计时器: %s", self._name, exc_info=True
            )
            self._stop_spinner()

    def _running_status_text(self) -> Text:
        """生成带闪烁效果的运行状态文本"""
        frame = BLINK_FRAMES[self._spinner_frame % len(BLINK_FRAMES)]
        return Text(frame, style=get_color("accent"))

    async def mount_interactive(self, widget: Widget) -> None:
        """将交互组件挂载到 Collapsible 内容区"""
        self._status = ToolStatus.WAITING
        self._stop_spinner()
        contents = self.query_one(Collapsible).query_one("Contents")
        await contents.mount(widget)
        self._interactive = widget
        # 展开 Collapsible 以显示交互组件，刷新标题显示等待圆圈
        self._update_title_label()
        self.query_one(Collapsible).collapsed = False

    def remove_interactive(self) -> None:
        """移除交互组件（decline 场景用）"""
        if self._interactive is not None and self._interactive.is_attached:
            self._interactive.remove()
            self._interactive = None

    def set_done(self, output: str = "") -> None:
        """标记工具执行完成，生成摘要行并折叠。

        摘要行（⎿ 文本）在挂载后由 on_mount 创建。若 block 已在 DOM 中
        则立即挂载；若尚未挂载（pending 状态），on_mount 时会检查并补挂。
        """
        self.remove_interactive()
        self._output_text = output
        self._status = ToolStatus.DONE
        self._stop_spinner()
        try:
            self._try_mount_summary()
            self._update_title_label()
            self.query_one(Collapsible).collapsed = True
        except NoMatches:
            logger.debug(
                "set_done: Collapsible 未挂载（compose 可能失败）: %s", self._name
            )

    def set_error(self, error: str = "") -> None:
        """标记工具执行错误，生成摘要行并折叠。"""
        self.remove_interactive()
        self._status = ToolStatus.ERROR
        self._error_text = error
        self._stop_spinner()
        try:
            self._try_mount_summary()
            self._update_title_label()
            self.query_one(Collapsible).collapsed = True
        except NoMatches:
            logger.debug(
                "set_error: Collapsible 未挂载（compose 可能失败）: %s", self._name
            )

    def set_interrupted(self) -> None:
        """标记工具执行被用户中断"""
        self.remove_interactive()
        self._status = ToolStatus.INTERRUPTED
        self._stop_spinner()
        try:
            self._update_title_label()
            self.query_one(Collapsible).collapsed = True
        except NoMatches:
            logger.debug("set_interrupted: Collapsible 未挂载: %s", self._name)

    @property
    def status(self) -> ToolStatus:
        return self._status

    @property
    def approval_mode(self) -> bool:
        return self._approval_mode

    def _get_symbol(self) -> Text:
        """根据状态返回带颜色的圆圈符号"""
        match self._status:
            case ToolStatus.RUNNING:
                return self._running_status_text()
            case ToolStatus.WAITING:
                return Text("○", style=get_color("warning"))
            case ToolStatus.DONE:
                return Text("●", style=get_color("success"))
            case ToolStatus.ERROR | ToolStatus.INTERRUPTED:
                return Text("●", style=get_color("error"))
            case _:
                return Text("●")

    def _build_title_markup(self) -> Text:
        """构建标题，圆圈颜色反映状态，标题文字保持默认色"""
        return Text.assemble(self._get_symbol(), " ", self._title_text)

    def _update_title_label(self) -> None:
        """直接更新 CollapsibleTitle 显示内容以保留 Rich 样式。

        Textual 8.x 的 Content.__eq__ 只比较文本不比较 spans，
        导致 reactive 系统认为值未变化而跳过更新。
        因此直接调用 update() 绕过 reactive 机制。
        """
        from textual.content import Content

        collapsible = self.query_one(Collapsible)
        title_widget = collapsible._title
        label = Content.from_text(self._build_title_markup())
        # collapsed_symbol / expanded_symbol 均为空字符串，
        # 为空时直接用 label，避免前面多出一个空格导致 ● 右移
        symbol = (
            title_widget.collapsed_symbol
            if title_widget.collapsed
            else title_widget.expanded_symbol
        )
        if symbol:
            title_widget.update(Content.assemble(symbol, " ", label))
        else:
            title_widget.update(label)

    async def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        """展开时懒渲染 output widget"""
        event.stop()
        await self._mount_output()

    async def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
        """折叠时销毁 output widget"""
        event.stop()
        await self._destroy_output()

    def _get_contents(self) -> Widget | None:
        """获取当前 ToolBlock 的 Collapsible Contents 容器"""
        try:
            return self.query_one(Collapsible).query_one("Contents")
        except NoMatches:
            logger.warning("Contents not found in ToolBlock: %s", self._name)
            return None

    def _mount_summary(self, *, is_error: bool) -> None:
        """生成 ⎿ 摘要行并同步挂载到 Contents。

        摘要行在工具完成时创建，作为展开态的第一行始终可见。
        使用 mount（同步）确保在 set_done/set_error 中立即生效。
        """
        contents = self._get_contents()
        if contents is None:
            return
        # 避免重复挂载
        if any(w.has_class("tool-summary") for w in contents.children):
            return

        output = self._error_text if is_error else self._output_text
        summary_text = self._renderer.render_summary(
            self._args, output, is_error=is_error
        )
        if not summary_text:
            return

        styled = Text()
        styled.append("⎿ ", style=get_color("text_muted"))
        styled.append(summary_text, style=get_color("text_muted"))
        widget = Static(styled, classes="tool-summary", markup=False)

        # 插入到 Contents 最前面（参数 widget 之前），确保紧跟标题行
        first_child = contents.children[0] if contents.children else None
        if first_child is not None:
            contents.mount(widget, before=first_child)
        else:
            contents.mount(widget)

        # 隐藏空的参数 widget，避免标题和摘要之间出现空白行
        for w in contents.children:
            if w.has_class("tool-args"):
                rendered = w.render()
                plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
                if not plain.strip():
                    w.display = False
                break

    def _try_mount_summary(self) -> None:
        """尝试挂载摘要行。

        若 block 尚未挂载到 DOM（pending 状态），_get_contents() 返回 None，
        此时跳过；on_mount 时会再次调用以补挂。
        """
        is_error = self._status in (ToolStatus.ERROR, ToolStatus.INTERRUPTED)
        self._mount_summary(is_error=is_error)

    async def _mount_output(self) -> None:
        """展开时按需渲染详情层 output widget（摘要行下方）"""
        contents = self._get_contents()
        if contents is None:
            return
        # 仅检查直接子节点，避免误判嵌套 ToolBlock 的 .tool-output
        if any(w.has_class("tool-output") for w in contents.children):
            return

        widget: Widget | None = None
        if self._error_text:
            widget = Static(
                Text(self._error_text, style=get_color("text_muted")),
                classes="tool-output",
                markup=False,
            )
        elif self._output_text:
            try:
                widget = self._renderer.render_output(self._output_text)
                widget.add_class("tool-output")
            except Exception:
                logger.warning(
                    "Renderer failed for ToolBlock %s, showing raw output",
                    self._name,
                    exc_info=True,
                )
                widget = Static(
                    Text(self._output_text[:2000], style=get_color("text_muted")),
                    classes="tool-output",
                    markup=False,
                )

        if widget is not None:
            try:
                await contents.mount(widget)
            except Exception:
                logger.warning(
                    "Failed to mount output widget for ToolBlock: %s",
                    self._name,
                    exc_info=True,
                )

    async def _destroy_output(self) -> None:
        """折叠时移除详情层 output widget（保留摘要行，不影响嵌套 ToolBlock）"""
        contents = self._get_contents()
        if contents is None:
            return
        for w in list(contents.children):
            if w.has_class("tool-output"):
                try:
                    await w.remove()
                except Exception:
                    logger.warning(
                        "Failed to remove output widget in ToolBlock: %s",
                        self._name,
                        exc_info=True,
                    )

    @property
    def subagent_log(self) -> Vertical | None:
        """获取子代理日志容器（仅 agent 工具有效）"""
        if not self._is_agent:
            return None
        try:
            return self.query_one(f"#subagent-log-{id(self)}", Vertical)
        except NoMatches:
            logger.debug("Subagent log container not found: %s", self._name)
            return None

    def reset_for_retry(self) -> None:
        """重置 agent block 的 UI 状态以便在 cancel/reject 后复用。

        清空子代理日志容器的子节点，恢复 RUNNING 状态和 spinner。
        子代理的数据状态由 SubagentTracker 管理。
        """
        # 不清空子代理日志内容 — 保留历史记录。
        # DOM 清理推迟到 _handle_tool_start 的 remap 分支，
        # 仅在确认有新周期时才清空。
        self._error_text = ""
        self._output_text = ""
        # 确保状态为 RUNNING
        self._status = ToolStatus.RUNNING
        self._start_spinner(interval=0.5)
        try:
            self._update_title_label()
            self.query_one(Collapsible).collapsed = True
        except NoMatches:
            logger.debug("reset_for_retry: Collapsible not found: %s", self._name)

    def on_ask_dialog_tab_changed(self, event) -> None:
        """阻止 AskDialog TabChanged 事件冒泡"""
        event.stop()

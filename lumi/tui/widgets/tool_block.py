"""工具调用块组件 - 可折叠，显示运行状态，集成渲染器"""

from __future__ import annotations

import logging
from enum import StrEnum

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.utils import SPINNER_FRAMES, SpinnerMixin, escape_markup
from lumi.tui.renderers.default import DefaultRenderer
from lumi.tui.theme import get_color

logger = logging.getLogger(__name__)

# 回退用默认渲染器实例
_FALLBACK_RENDERER = DefaultRenderer()


class ToolStatus(StrEnum):
    """工具执行状态"""

    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class ToolBlock(Vertical, SpinnerMixin):
    """工具调用块 - 可折叠，显示工具名称、参数和输出

    通过 ToolDisplayRegistry 获取对应渲染器，生成专属的标题、参数和输出展示。
    审批模式下自动展开详细内容，便于用户审阅。
    运行中时标题行显示 spinner 动画。
    """

    DEFAULT_CSS = """
    ToolBlock {
        margin: 0 1 1 0;
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
    }

    ToolBlock Contents {
        padding: 0 0 0 1;
        margin: 0 0 0 1;
        border-left: solid $text-muted;
    }

    ToolBlock .tool-args {
        padding: 0 1;
        color: $text-muted;
    }

    ToolBlock .tool-output {
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, name: str, args: dict, approval_mode: bool = False) -> None:
        super().__init__(classes="tool-block")
        self._name = name
        self._args = args
        self._approval_mode = approval_mode
        self._status = ToolStatus.RUNNING
        self._interactive: Widget | None = None

        self._renderer = get_renderer(name)

        # 预计算标题文本（不随 spinner 变化，避免每帧重复调用 render_title）
        try:
            self._title_text = self._renderer.render_title(name, args)
        except Exception:
            logger.warning(
                "渲染器 render_title 失败，回退到默认渲染器: %s", name, exc_info=True
            )
            self._title_text = _FALLBACK_RENDERER.render_title(name, args)

    def compose(self) -> ComposeResult:
        """组合子组件：标题 + 参数区域 + 输出区域"""
        title_markup = self._build_title_markup()
        collapsed = not self._approval_mode  # 审批模式自动展开

        # 获取参数区域 Widget
        try:
            args_widget = self._renderer.render_args(
                self._args, approval_mode=self._approval_mode
            )
        except Exception:
            logger.warning(
                "渲染器 render_args 失败，回退到默认渲染器: %s",
                self._name,
                exc_info=True,
            )
            args_widget = _FALLBACK_RENDERER.render_args(self._args)

        with Collapsible(
            title=title_markup,
            collapsed=collapsed,
            collapsed_symbol="",
            expanded_symbol="",
            id=f"tool-{id(self)}",
        ):
            yield args_widget
            yield Static(
                "",
                classes="tool-output",
                id=f"tool-output-{id(self)}",
                markup=False,
            )

    def on_mount(self) -> None:
        """挂载后启动 spinner 动画"""
        if self._status == ToolStatus.RUNNING:
            self._start_spinner()

    def _on_spinner_tick(self, frame_char: str) -> None:
        """SpinnerMixin 回调：更新标题行 spinner"""
        if self._status != ToolStatus.RUNNING:
            return
        try:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._build_title_markup()
        except NoMatches:
            pass  # 尚未挂载，下次再试
        except Exception:
            logger.warning(
                "spinner 更新失败，停止计时器: %s", self._name, exc_info=True
            )
            self._stop_spinner()

    def _running_status_text(self) -> str:
        """生成带 spinner 的运行状态文本"""
        frame = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
        return f"[{get_color('text_muted')}]{frame}[/]"

    async def mount_interactive(self, widget: Widget) -> None:
        """将交互组件挂载到 Collapsible 内容区（output Static 之前）"""
        self._stop_spinner()
        output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
        parent = output_widget.parent
        await parent.mount(widget, before=output_widget)
        self._interactive = widget
        # 展开 Collapsible 以显示交互组件
        collapsible = self.query_one(Collapsible)
        collapsible.collapsed = False

    def remove_interactive(self) -> None:
        """移除交互组件（decline 场景用）"""
        if self._interactive is not None and self._interactive.is_attached:
            self._interactive.remove()
            self._interactive = None

    def set_done(self, output: str = "") -> None:
        """标记工具执行完成"""
        # 如果交互组件仍在 DOM 中，先移除
        self.remove_interactive()
        self._status = ToolStatus.DONE
        self._stop_spinner()
        try:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._build_title_markup()
            collapsible.collapsed = True
        except NoMatches:
            logger.debug(
                "set_done: Collapsible 未挂载（compose 可能失败）: %s", self._name
            )
            return

        if output:
            output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
            try:
                new_widget = self._renderer.render_output(output)
                # 从渲染器返回的 Static 中提取 renderable 更新现有 widget
                output_widget.update(new_widget.renderable)
            except Exception:
                logger.warning(
                    "渲染器 render_output 失败，回退到默认渲染器: %s",
                    self._name,
                    exc_info=True,
                )
                display = output if len(output) <= 500 else output[:500] + "\n..."
                output_widget.update(display)

    def set_error(self, error: str = "") -> None:
        """标记工具执行错误"""
        self._status = ToolStatus.ERROR
        self._stop_spinner()
        try:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._build_title_markup()
        except NoMatches:
            logger.debug(
                "set_error: Collapsible 未挂载（compose 可能失败）: %s", self._name
            )
            return
        if error:
            output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
            output_widget.update(Text(error, style=get_color("text_muted")))

    def set_interrupted(self) -> None:
        """标记工具执行被用户中断"""
        self.remove_interactive()
        self._status = ToolStatus.INTERRUPTED
        self._stop_spinner()
        try:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._build_title_markup()
            collapsible.collapsed = True
        except NoMatches:
            logger.debug("set_interrupted: Collapsible 未挂载: %s", self._name)

    @property
    def status(self) -> ToolStatus:
        return self._status

    @property
    def approval_mode(self) -> bool:
        return self._approval_mode

    def _get_symbol(self) -> str:
        """根据状态返回带颜色的圆圈符号"""
        match self._status:
            case ToolStatus.RUNNING:
                return self._running_status_text()
            case ToolStatus.DONE:
                return f"[{get_color('success')}]●[/]"
            case ToolStatus.ERROR | ToolStatus.INTERRUPTED:
                return f"[{get_color('error')}]●[/]"
            case _:
                return "●"

    def _build_title_markup(self) -> str:
        """构建标题 markup，圆圈颜色反映状态，标题文字保持白色"""
        hint = (
            f" [{get_color('text_muted')}](click to expand)[/]"
            if self._status != ToolStatus.RUNNING
            else ""
        )
        return f"{self._get_symbol()} {escape_markup(self._title_text)}{hint}"

    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        """折叠/展开时更新标题"""
        event.stop()

    def on_ask_dialog_tab_changed(self, event) -> None:
        """阻止 AskDialog TabChanged 事件冒泡"""
        event.stop()

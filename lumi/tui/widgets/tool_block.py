"""工具调用块组件 - 可折叠，显示运行状态，集成渲染器"""

from __future__ import annotations

import logging
from enum import StrEnum

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Static

from lumi.tui.renderers import get as get_renderer
from lumi.tui.renderers.utils import SPINNER_FRAMES
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


class ToolBlock(Vertical):
    """工具调用块 - 可折叠，显示工具名称、参数和输出

    通过 ToolDisplayRegistry 获取对应渲染器，生成专属的标题、参数和输出展示。
    审批模式下自动展开详细内容，便于用户审阅。
    运行中时标题行显示 spinner 动画。
    """

    DEFAULT_CSS = """
    ToolBlock {
        margin: 0 1 0 2;
        height: auto;
    }

    ToolBlock Collapsible {
        background: transparent;
        border: none;
        padding: 0;
    }

    ToolBlock .tool-args {
        padding: 0 1;
        color: $text-muted;
    }

    ToolBlock .tool-output {
        padding: 0 1;
        margin: 1 0 0 0;
        color: $foreground;
    }
    """

    def __init__(self, name: str, args: dict, approval_mode: bool = False) -> None:
        super().__init__(classes="tool-block")
        self._name = name
        self._args = args
        self._approval_mode = approval_mode
        self._status = ToolStatus.RUNNING
        self._spinner_frame = 0
        self._spinner_timer = None

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
        title_markup = self._build_title_markup(self._running_status_text())
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
            title=title_markup, collapsed=collapsed, id=f"tool-{id(self)}"
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
            self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        """更新 spinner 帧"""
        if self._status != ToolStatus.RUNNING:
            return
        self._spinner_frame += 1
        try:
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._build_title_markup(self._running_status_text())
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
        return f"[{get_color('text_muted')}]{frame} Running...[/]"

    def _stop_spinner(self) -> None:
        """停止 spinner 动画"""
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def set_done(self, output: str = "") -> None:
        """标记工具执行完成"""
        self._status = ToolStatus.DONE
        self._stop_spinner()
        collapsible = self.query_one(Collapsible)
        collapsible.title = self._build_title_markup(f"[{get_color('success')}]Done[/]")
        collapsible.collapsed = True

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
        collapsible = self.query_one(Collapsible)
        collapsible.title = self._build_title_markup(f"[{get_color('error')}]Error[/]")
        if error:
            output_widget = self.query_one(f"#tool-output-{id(self)}", Static)
            output_widget.update(Text(error, style=get_color("error")))

    @property
    def approval_mode(self) -> bool:
        return self._approval_mode

    def _build_title_markup(self, status: str) -> str:
        """构建标题 markup，格式: [bold #ffcc00]渲染器标题[/]  状态"""
        return f"[bold {get_color('accent')}]{self._title_text}[/]  {status}"

"""顶部标题区块 - 像素太阳 Logo + 左右分栏布局"""

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from lumi import __version__
from lumi.tui.theme import get_color


def _build_logo() -> str:
    """构建主题感知的 Logo Rich markup。"""
    dim = get_color("accent_dim")
    acc = get_color("accent")
    # Logo 中心白色区域保持 #ffffff，不随主题变化
    wht = "#ffffff"
    return (
        f"[{dim}]         .         [/]\n"
        f"[{dim}]    .  [/][{acc}]  ▄  [/][{dim}]  .    [/]\n"
        f"[{acc}]    . ▄█████▄ .    [/]\n"
        f"[{acc}]     ███[{wht}]░░░[/][{acc}]███     [/]\n"
        f"[{acc}] - ████[{wht}]░░░░░[/][{acc}]████ - [/]\n"
        f"[{acc}]     ███[{wht}]░░░[/][{acc}]███     [/]\n"
        f"[{acc}]    . ▀█████▀ .    [/]\n"
        f"[{dim}]    .  [/][{acc}]  ▀  [/][{dim}]  .    [/]\n"
        f"[{dim}]         .         [/]"
    )


class TitleBlock(Static):
    """整个顶部标题区块"""

    DEFAULT_CSS = """
    TitleBlock {
        height: auto;
        margin: 1 2;
        border-title-style: bold;
        border: round $accent;
        border-title-color: $accent;
    }

    #title-row {
        height: auto;
    }

    #left-panel {
        width: auto;
        min-width: 28;
        height: auto;
        padding: 1 2;
        text-align: center;
        content-align: center middle;
        border-right: solid $border;
    }

    #right-col {
        width: 1fr;
        height: auto;
    }

    #right-top {
        height: auto;
        padding: 1 2;
        border-bottom: solid $border;
    }

    #right-bottom {
        height: auto;
        padding: 1 2;
    }
    """

    def __init__(
        self,
        model_name: str = "",
        project_path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._model_name = model_name
        if project_path:
            self._project_path = project_path
        else:
            cwd = Path(os.getcwd())
            try:
                self._project_path = f"~/{cwd.relative_to(Path.home())}"
            except ValueError:
                self._project_path = str(cwd)

    def compose(self) -> ComposeResult:
        accent = get_color("accent")
        success = get_color("success")
        text_primary = get_color("text_primary")

        with Horizontal(id="title-row"):
            # ── 左侧: Logo + 信息 ──
            yield Static(
                f"{_build_logo()}\n"
                f"[bold {text_primary}]lumi[/] [dim]v{__version__}[/]\n"
                f"[dim]{self._model_name} · {self._project_path}[/]",
                id="left-panel",
            )
            # ── 右侧: 上下分区 ──
            with Vertical(id="right-col"):
                yield Static(
                    f"[{accent}]Tips for getting started[/]\n"
                    f"[{success}]✔[/] Run [bold]/init[/] to create a config file\n"
                    f"[{success}]✔[/] Use [bold]/help[/] to see available commands",
                    id="right-top",
                )
                yield Static(
                    f"[{accent}]Recent activity[/]\n[dim]No recent activity[/]",
                    id="right-bottom",
                )

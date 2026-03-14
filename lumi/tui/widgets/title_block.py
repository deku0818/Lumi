"""顶部标题区块 - 像素太阳 Logo + 左右分栏布局"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from lumi.tui.renderers.utils import escape_markup as escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from lumi import __version__
from lumi.tui.theme import get_color

if TYPE_CHECKING:
    from lumi.tui.session_store import SessionSummary


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
        min-width: 40;
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
        recent_sessions: list[SessionSummary] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._model_name = model_name
        self._recent_sessions = recent_sessions or []
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
                f"[dim]{escape(self._model_name)}[/]\n"
                f"[dim]{escape(self._project_path)}[/]",
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
                    self._build_recent_activity(accent),
                    id="right-bottom",
                )

    _MAX_RECENT: int = 3

    def _build_recent_activity(self, accent: str) -> str:
        """构建最近会话活动的 Rich markup 文本。

        Args:
            accent: 主题强调色

        Returns:
            Rich markup 格式的最近活动文本
        """
        header = f"[{accent}]Recent activity[/]"
        if not self._recent_sessions:
            return f"{header}\n[dim]No recent activity[/]"

        lines = [header]
        for s in self._recent_sessions[: self._MAX_RECENT]:
            msg = escape(s.first_message)
            if len(msg) > 30:
                msg = msg[:27] + "..."
            lines.append(f"[dim]{s.display_time}[/]  {msg}")
        return "\n".join(lines)

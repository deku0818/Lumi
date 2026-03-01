"""顶部标题区块 - 像素太阳 Logo + 左右分栏布局"""

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from lumi import __version__


LUMI_LOGO = """\
[#666666]         .         [/]
[#666666]    .  [/][#ffcc00]  ▄  [/][#666666]  .    [/]
[#ffcc00]    . ▄█████▄ .    [/]
[#ffcc00]     ███[#fff]░░░[/][#ffcc00]███     [/]
[#ffcc00] - ████[#fff]░░░░░[/][#ffcc00]████ - [/]
[#ffcc00]     ███[#fff]░░░[/][#ffcc00]███     [/]
[#ffcc00]    . ▀█████▀ .    [/]
[#666666]    .  [/][#ffcc00]  ▀  [/][#666666]  .    [/]
[#666666]         .         [/]"""


class TitleBlock(Static):
    """整个顶部标题区块"""

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
        with Horizontal(id="title-row"):
            # ── 左侧: Logo + 信息 ──
            yield Static(
                f"{LUMI_LOGO}\n"
                f"[bold #ffffff]lumi[/] [dim]v{__version__}[/]\n"
                f"[dim]{self._model_name} · {self._project_path}[/]",
                id="left-panel",
            )
            # ── 右侧: 上下分区 ──
            with Vertical(id="right-col"):
                yield Static(
                    "[#ffcc00]Tips for getting started[/]\n"
                    "[green]✔[/] Run [bold]/init[/] to create a config file\n"
                    "[green]✔[/] Use [bold]/help[/] to see available commands",
                    id="right-top",
                )
                yield Static(
                    "[#ffcc00]Recent activity[/]\n[dim]No recent activity[/]",
                    id="right-bottom",
                )

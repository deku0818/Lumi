"""Locate native CLIs without relying on the parent coding agent's environment."""

import os
import shutil
import sys
from pathlib import Path
from typing import Literal


def resolve_cli(name: Literal["codex", "claude"]) -> str:
    if found := shutil.which(name):
        return found
    candidates = [Path.home() / ".local" / "bin" / name]
    if name == "codex":
        if configured := os.environ.get("CODEX_CLI_PATH"):
            candidates.insert(0, Path(configured))
        if sys.platform == "darwin":
            bundled = "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
            candidates.extend(
                root / bundled
                for root in (
                    Path("/Applications"),
                    Path.home() / "Applications",
                )
            )
    for candidate in candidates:
        if found := shutil.which(str(candidate)):
            return found
    raise RuntimeError(
        f"运行 Lumi 的机器上未找到 {name} 可执行程序。"
        f"请安装对应 CLI 并加入 PATH，然后重启 Lumi。"
    )

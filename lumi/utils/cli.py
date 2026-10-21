"""Small, read-only CLI probes shared by environment checks."""

from __future__ import annotations

import re
import subprocess


def run_cli(cmd: list[str], timeout: int = 30) -> tuple[bool, str]:
    """显式 UTF-8 解码，避免中文 Windows 的系统 locale 破坏 CLI JSON 输出。"""
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return process.returncode == 0, process.stdout or process.stderr
    except Exception as error:
        return False, str(error)


def version_from_output(output: str) -> str:
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else ""

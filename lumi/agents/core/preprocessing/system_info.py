"""系统信息注入模块

收集操作系统、Shell 等环境信息，由 :mod:`context_inject` 包装为 ``<system-reminder>``
块注入到用户消息中，使 LLM 能感知运行环境以提供更精准的建议。
"""

from __future__ import annotations

import os
import platform
import sys

from lumi.agents.permissions.workspace import get_authorized_directory


def _detect_shell() -> str:
    """检测当前 shell 名称。

    Windows 恒为 cmd：这条信息是给模型写命令用的，必须与 ``bash`` 工具实际 spawn 的
    shell（``LocalShellSession`` 的 cmd.exe）一致。按机器上装没装 pwsh 来报，模型会
    写出一堆 PowerShell 语法丢进 cmd 里执行。
    """
    if sys.platform == "win32":
        return "cmd"
    # Unix
    shell = os.environ.get("SHELL", "")
    return os.path.basename(shell) if shell else "sh"


def collect_system_info() -> dict[str, str]:
    """收集当前系统环境信息。"""
    return {
        "os": platform.platform(terse=True),
        "python": platform.python_version(),
        "shell": _detect_shell(),
        # 本会话授权主目录（per-run）：项目随会话绑定，不再是进程级 os.getcwd()
        "cwd": str(get_authorized_directory()),
    }


def system_info_body(info: dict[str, str] | None = None) -> str:
    """系统信息条目行（digest 与全量/变更重发共用同一 body）。"""
    if info is None:
        info = collect_system_info()
    return "\n".join(f"- {k}: {v}" for k, v in info.items() if v)

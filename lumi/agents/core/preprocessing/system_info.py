"""系统信息注入模块

收集操作系统、Shell 等环境信息，格式化为 ``<system-reminder>`` 块注入到用户消息中，
使 LLM 能感知运行环境以提供更精准的建议。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys

from langchain_core.messages import HumanMessage

from lumi.agents.core.node_helpers.messages import inject_text_into_message


def _detect_shell() -> str:
    """检测当前 shell 名称。"""
    if sys.platform == "win32":
        for candidate in ("pwsh", "powershell"):
            if shutil.which(candidate):
                return candidate
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
        "cwd": os.getcwd(),
    }


def format_system_reminder(info: dict[str, str] | None = None) -> str:
    """将系统信息格式化为 ``<system-reminder>`` 块。"""
    if info is None:
        info = collect_system_info()

    body = "\n".join(f"- {k}: {v}" for k, v in info.items() if v)
    return f"<system-reminder>\n用户当前系统环境信息\n{body}\n</system-reminder>\n"


def inject_system_info_into_message(
    message: HumanMessage,
    info: dict[str, str] | None = None,
) -> HumanMessage:
    """将系统信息 ``<system-reminder>`` 块注入到用户消息 content 最前面，返回新消息。"""
    return inject_text_into_message(message, format_system_reminder(info))

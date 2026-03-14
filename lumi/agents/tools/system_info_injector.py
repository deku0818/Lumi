"""系统信息注入模块

收集用户操作系统、架构、Shell 等环境信息，
格式化为 <system-reminder> 块注入到用户消息中，
使 LLM 能感知用户的运行环境以提供更精准的建议。
"""

from __future__ import annotations

import os
import platform
import shutil

from langchain_core.messages import HumanMessage

from lumi.agents.core.message_tools import inject_text_into_message


def collect_system_info() -> dict[str, str]:
    """收集当前系统环境信息。

    Returns:
        包含 os、version、arch、shell、cwd 等键值对的字典
    """
    import sys

    if sys.platform == "win32":
        # Windows: 优先检测 PowerShell，再回退到 cmd
        if shutil.which("pwsh"):
            shell = "pwsh"
        elif shutil.which("powershell"):
            shell = "powershell"
        else:
            shell = "cmd"
    else:
        # Unix: 从 SHELL 环境变量获取
        shell = os.environ.get("SHELL", "")
        if shell:
            shell = os.path.basename(shell)
        else:
            shell = "sh"

    return {
        "os": platform.platform(terse=True),
        "python": platform.python_version(),
        "shell": shell,
        "cwd": os.getcwd(),
    }


def format_system_reminder(info: dict[str, str] | None = None) -> str:
    """将系统信息格式化为 <system-reminder> 块。

    Args:
        info: 系统信息字典，为 None 时自动收集

    Returns:
        格式化后的 system-reminder 文本
    """
    if info is None:
        info = collect_system_info()

    lines = [f"- {k}: {v}" for k, v in info.items() if v]
    body = "\n".join(lines)
    return f"<system-reminder>\n用户当前系统环境信息\n{body}\n</system-reminder>\n"


def inject_system_info_into_message(
    message: HumanMessage,
    info: dict[str, str] | None = None,
) -> HumanMessage:
    """将系统信息 system-reminder 块注入到用户消息中。

    插入到用户原始内容之前，返回新的 HumanMessage（不可变原则）。

    Args:
        message: 原始用户消息
        info: 系统信息字典，为 None 时自动收集

    Returns:
        注入后的新 HumanMessage
    """
    reminder_text = format_system_reminder(info)
    return inject_text_into_message(message, reminder_text)

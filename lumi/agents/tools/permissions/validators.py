"""Bash 命令安全校验器

返回危险命令的警告信息，用于审批 UI 展示。
不阻断执行——仅提供信息辅助用户决策。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_Level = Literal["warning", "danger"]

# 危险命令模式：(编译后正则, 级别, 中文警告)
_DANGER_PATTERNS: tuple[tuple[re.Pattern[str], _Level, str], ...] = (
    (
        re.compile(r"git\s+push\s+.*(-f|--force)"),
        "danger",
        "Force push 可能覆盖远程提交历史",
    ),
    (
        re.compile(r"git\s+reset\s+--hard"),
        "danger",
        "会丢失未提交的本地更改",
    ),
    (
        re.compile(r"git\s+clean\s+-[a-zA-Z]*f"),
        "danger",
        "会删除未跟踪的文件",
    ),
    (
        re.compile(r"curl\s.*\|\s*(?:ba)?sh"),
        "danger",
        "从网络下载并直接执行脚本",
    ),
    (
        re.compile(r"wget\s.*\|\s*(?:ba)?sh"),
        "danger",
        "从网络下载并直接执行脚本",
    ),
    (
        re.compile(r"chmod\s+777\s"),
        "warning",
        "chmod 777 会开放所有权限",
    ),
    (
        re.compile(r">\s*/dev/sd"),
        "danger",
        "直接写入块设备",
    ),
)


@dataclass(frozen=True)
class SafetyWarning:
    """安全警告"""

    level: Literal["warning", "danger"]
    message: str


def validate_bash_command(command: str) -> list[SafetyWarning]:
    """校验 bash 命令，返回安全警告列表。

    纯字符串正则匹配，不执行任何命令。

    Args:
        command: bash 命令字符串

    Returns:
        匹配的安全警告列表（可能为空）
    """
    if not command:
        return []

    warnings: list[SafetyWarning] = []
    for pattern, level, message in _DANGER_PATTERNS:
        if pattern.search(command):
            warnings.append(SafetyWarning(level=level, message=message))
    return warnings

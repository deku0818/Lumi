"""消息文本清理 — 统一的 XML 标签过滤和用户输入还原。

session_store 和 message_restore 共用此模块，避免重复的正则和清理逻辑。
"""

from __future__ import annotations

import re

# 从消息中提取命令名和用户输入的正则
_COMMAND_NAME_RE: re.Pattern[str] = re.compile(
    r"<command-name>(/[\w-]+)</command-name>"
)
_USER_INPUT_RE: re.Pattern[str] = re.compile(
    r"<user-input>(.*?)</user-input>", re.DOTALL
)

# 需过滤的注入块标签
_INJECTED_BLOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL),
    re.compile(r"<summary>.*?</summary>\s*", re.DOTALL),
    re.compile(r"<command-name>.*?</command-name>\s*", re.DOTALL),
    re.compile(r"<command-type>.*?</command-type>\s*", re.DOTALL),
    re.compile(r"<command-args>.*?</command-args>\s*", re.DOTALL),
]


def strip_injected_blocks(raw: str) -> str:
    """过滤消息中所有注入块，只保留用户实际输入。"""
    result = raw
    for pattern in _INJECTED_BLOCK_PATTERNS:
        result = pattern.sub("", result)
    return result.strip()


def extract_display_text(raw: str) -> str:
    """清理消息中的 XML 标签，还原用户可读文本。

    技能命令消息从 <command-name> 和 <user-input> 标签还原用户输入，
    非技能消息则过滤掉所有注入块（system-reminder、summary 等）。

    Args:
        raw: 原始消息文本

    Returns:
        清理后的显示文本，纯注入内容返回空字符串
    """
    cmd_match = _COMMAND_NAME_RE.search(raw)
    if cmd_match:
        cmd = cmd_match.group(1)
        ui_match = _USER_INPUT_RE.search(raw)
        if ui_match:
            user_input = ui_match.group(1).strip()
            return f"{cmd} {user_input}" if user_input else cmd
        # command-name 存在但无 user-input，检查是否有剩余用户文本
        cleaned = strip_injected_blocks(raw)
        return cleaned if cleaned else cmd

    return strip_injected_blocks(raw)

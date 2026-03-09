"""命令解析工具函数 - 命令模式检测、前缀提取和输入解析"""

from __future__ import annotations


def is_command_mode(text: str) -> bool:
    """检测文本是否处于命令模式。

    当且仅当文本以 "/" 开头时返回 True。

    Args:
        text: 用户输入的文本

    Returns:
        文本以 "/" 开头返回 True，否则 False
    """
    return text.startswith("/")


def extract_command_prefix(text: str) -> str:
    """提取命令前缀（"/" 后到第一个空格之间的子串）。

    示例:
        "/skills foo" -> "skills"
        "/sk" -> "sk"
        "/" -> ""

    Args:
        text: 以 "/" 开头的用户输入文本

    Returns:
        去掉 "/" 后到第一个空格（或字符串末尾）之间的子串
    """
    # 去掉 "/" 前缀
    without_slash = text[1:]
    # 取第一个空格之前的部分
    space_idx = without_slash.find(" ")
    if space_idx == -1:
        return without_slash
    return without_slash[:space_idx]


def parse_command_input(text: str) -> tuple[str, str]:
    """解析命令输入，返回 (命令名, 额外文本)。

    示例:
        "/skills hello world" -> ("skills", "hello world")
        "/skills" -> ("skills", "")
        "/" -> ("", "")

    Args:
        text: 以 "/" 开头的用户输入文本

    Returns:
        (命令名, 额外文本) 的元组
    """
    command_name = extract_command_prefix(text)
    # 额外文本 = "/" + 命令名 之后的部分（去掉前导空格）
    rest = text[1 + len(command_name) :]
    extra_text = rest.lstrip(" ") if rest else ""
    return command_name, extra_text

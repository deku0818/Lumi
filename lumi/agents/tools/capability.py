"""工具类型与 bash 复合命令拆分；不提供权限绕过规则。"""

from __future__ import annotations

from lumi.agents.tools.shell_syntax import parse_command


def split_compound_command(command: str) -> list[str]:
    """拆出命令里会执行的全部子命令原文（逐个参与权限规则匹配）。

    分隔符含 &&、||、;、|、&、换行与子 shell 括号；命令替换 / 进程替换里的命令也作为
    独立子命令拆出，引号、转义、注释与 heredoc 正文按 bash 规则处理（见 shell_syntax）。
    """
    texts = [seg.text for seg in parse_command(command)]
    return texts or ([command.strip()] if command.strip() else [])


def is_file_edit_tool(tool_name: str) -> bool:
    """accept_edits 模式下可直接放行的文件编辑工具。"""
    return tool_name in {"write", "edit"}

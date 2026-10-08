"""工具权限控制系统 - 工作区边界检查器

检查工具调用涉及的路径是否在已授权的工作区范围内。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lumi.agents.permissions.matcher import COMMAND_ARG_KEYS, extract_arg
from lumi.agents.permissions.models import PATH_ARG_KEYS
from lumi.agents.tools.shell_syntax import DYNAMIC, parse_command
from lumi.utils.logger import logger

# bash 写入类命令：非选项参数都是写入目标
_BASH_WRITE_COMMANDS: frozenset[str] = frozenset(
    {"rm", "rmdir", "mv", "mkdir", "touch", "chmod", "chown", "tee"}
)
# 切换目录的命令：不写，但其后的相对路径都落在那里（边界检查计入，写保护不计）
_BASH_CWD_COMMANDS: frozenset[str] = frozenset({"cd", "pushd"})
# 只有最后一个参数是写入目标的命令（其余是读取来源）
_BASH_DEST_COMMANDS: frozenset[str] = frozenset({"cp", "ln"})

# 值在执行时才确定的路径（变量 / 命令替换）：无法静态定位，按越界处理
_UNKNOWN_PATH = Path("/⟨dynamic-path⟩")

_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

# 列表型路径参数键名（如 artifacts 的 filepaths），逐项提取参与边界检查
_PATH_LIST_ARG_KEYS: tuple[str, ...] = ("filepaths",)


class WorkspaceBoundary:
    """工作区边界检查器

    检查工具调用涉及的文件/目录路径是否在已授权的工作区范围内。
    默认工作区为项目根目录，可通过配置扩展。
    """

    def __init__(self, workspaces: list[Path]) -> None:
        """初始化工作区边界检查器。

        Args:
            workspaces: 已授权的工作区目录列表（绝对路径）
        """
        self._workspaces: list[Path] = []
        for ws in workspaces:
            try:
                self._workspaces.append(ws.resolve())
            except OSError:
                logger.warning("工作区路径解析失败: %s", ws)

    @property
    def workspaces(self) -> list[Path]:
        """已授权工作区列表（解析后绝对路径，项目根 / 主目录在首位）。"""
        return list(self._workspaces)

    def is_within_boundary(self, path: str | Path) -> bool:
        """检查路径是否在任一工作区边界内。

        Args:
            path: 待检查的文件/目录路径

        Returns:
            True 表示在边界内，False 表示超出边界
        """
        try:
            # 先按 shell 语义展开 ~：bash 命令里的 ~/x 在执行时展开到家目录，
            # 不展开会被当作工作区内的相对路径而绕过边界检查
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            logger.warning("路径解析异常，视为边界外: %s", path)
            return False

        for ws in self._workspaces:
            try:
                resolved.relative_to(ws)
                return True
            except ValueError:
                continue
        return False

    def extract_paths_from_tool_call(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> list[Path]:
        """从工具调用参数中提取涉及的文件/目录路径。

        对于文件操作工具（read/write/edit/glob/grep），从参数中提取路径。
        对于 bash 工具，尝试从命令字符串中提取目标路径。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            提取到的路径列表；无法提取时返回空列表
        """
        if tool_name == "bash":
            return self._extract_bash_paths(tool_args)
        return self._extract_file_tool_paths(tool_args)

    def _extract_file_tool_paths(self, tool_args: dict[str, Any]) -> list[Path]:
        """从文件操作工具参数中提取路径。"""
        paths: list[Path] = []
        for key in PATH_ARG_KEYS:
            value = tool_args.get(key)
            if isinstance(value, str) and value:
                paths.append(Path(value))
        for key in _PATH_LIST_ARG_KEYS:
            value = tool_args.get(key)
            if isinstance(value, list):
                paths.extend(Path(v) for v in value if isinstance(v, str) and v)
        return paths

    def _extract_bash_paths(self, tool_args: dict[str, Any]) -> list[Path]:
        """bash 命令的写入目标；值未知的按越界处理。未识别的命令不提取（视为边界内）。"""
        command = extract_arg(tool_args, COMMAND_ARG_KEYS)
        if command is None or not command.strip():
            return []
        return [
            _UNKNOWN_PATH if DYNAMIC in t else Path(t)
            for t in bash_write_targets(command, with_cwd=True)
        ]


def bash_write_targets(command: str, *, with_cwd: bool = False) -> list[str]:
    """bash 命令的写入目标原文（值未知处含 ``DYNAMIC``；边界与写保护检查共用）。

    取各子命令（含替换 / 子 shell 内的）的写重定向与写入类命令的路径参数；读取来源
    （``<`` 输入、``cp`` 的源、``cat`` 的参数）不算；``with_cwd`` 时计入 cd 的目标目录。
    相对路径的基准是项目根——shell 此前 cd 到别处的情形无从得知，属尽力而为。
    """
    targets: list[str] = []
    for seg in parse_command(command):
        targets.extend(seg.writes)
        words = list(seg.words)
        while words and (words[0] == "sudo" or _ASSIGNMENT.match(words[0])):
            words.pop(0)
        if not words:
            continue
        name = Path(words[0]).name
        operands = [w for w in words[1:] if not w.startswith("-")]
        if name in _BASH_DEST_COMMANDS:
            targets.extend(operands[-1:])
        elif (
            name in _BASH_WRITE_COMMANDS
            or (with_cwd and name in _BASH_CWD_COMMANDS)
            or (name == "sed" and _sed_in_place(words))
        ):
            targets.extend(operands)
    return targets


def _sed_in_place(words: list[str]) -> bool:
    return any(
        w.startswith("--in-place") or (w[:1] == "-" and w[1:2] != "-" and "i" in w)
        for w in words[1:]
    )

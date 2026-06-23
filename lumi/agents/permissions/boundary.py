"""工具权限控制系统 - 工作区边界检查器

检查工具调用涉及的路径是否在已授权的工作区范围内。
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from lumi.agents.permissions.models import PATH_ARG_KEYS
from lumi.utils.logger import logger

# bash 命令中常见的路径操作命令，匹配时提取所有非标志参数作为路径
_BASH_PATH_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "cd",
        "cp",
        "mv",
        "rm",
        "mkdir",
        "touch",
        "chmod",
        "chown",
    }
)

# 重定向符号：后面的 token 是文件路径，需要检查边界
_REDIRECT_TO_PATH: frozenset[str] = frozenset({">", ">>", "<", "2>", "2>>", "&>"})

# heredoc 符号：后面的 token 是定界符，不是路径
_HEREDOC_OPERATORS: frozenset[str] = frozenset({"<<", "<<<"})

# 命令分隔/管道符号：遇到后停止解析（后续是新命令）
_COMMAND_SEPARATORS: frozenset[str] = frozenset({"|", "||", "&&", ";", "&"})

# 列表型路径参数键名（如 present_files 的 filepaths），逐项提取参与边界检查
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
        """从 bash 命令中尝试提取目标路径。

        解析命令字符串，识别常见命令并提取路径参数。
        无法识别的命令返回空列表（视为边界内）。
        """
        command = tool_args.get("command") or tool_args.get("cmd")
        if not isinstance(command, str) or not command.strip():
            return []

        # 多行命令处理：仅在检测到 heredoc 操作符时截断，其他多行命令逐行解析
        lines = command.split("\n")
        parse_target = lines[0]
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            # 检测 heredoc 操作符，截断后续行（heredoc 内容不是路径）
            if "<<" in stripped:
                parse_target = "\n".join(lines[: i + 1])
                break
        else:
            # 无 heredoc：拼接所有行（处理反斜杠续行和多行命令）
            parse_target = " ".join(line.rstrip("\\").strip() for line in lines)

        try:
            parts = shlex.split(parse_target)
        except ValueError:
            logger.warning("bash 命令含无效引号，视为越界: %.200s", parse_target)
            return [Path("/⟨unparseable-command⟩")]

        if not parts:
            return []

        # 跳过前导的 env 变量赋值和 sudo
        cmd_start = 0
        for i, part in enumerate(parts):
            if part == "sudo" or "=" in part:
                cmd_start = i + 1
            else:
                break

        if cmd_start >= len(parts):
            return []

        cmd_name = Path(parts[cmd_start]).name
        if cmd_name not in _BASH_PATH_COMMANDS:
            return []

        # 过滤标志参数、shell 操作符，正确处理重定向和 heredoc
        raw_args = parts[cmd_start + 1 :]
        path_args: list[str] = []
        skip_next = False
        for arg in raw_args:
            if skip_next:
                skip_next = False
                continue
            if arg in _COMMAND_SEPARATORS:
                break  # 管道/分号后是新命令，停止解析
            if arg in _HEREDOC_OPERATORS:
                skip_next = True  # heredoc 定界符不是路径
                continue
            if arg in _REDIRECT_TO_PATH:
                continue  # 跳过符号本身，路径 token 在下轮迭代正常收集
            if arg.startswith("-"):
                continue
            path_args.append(arg)

        return [Path(a) for a in path_args]

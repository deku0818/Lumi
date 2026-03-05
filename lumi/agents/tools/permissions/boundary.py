"""工具权限控制系统 - 工作区边界检查器

检查工具调用涉及的路径是否在已授权的工作区范围内。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from lumi.utils.logger import logger

# bash 命令中常见的路径参数位置提取规则
# 格式: (命令前缀, 路径参数索引)
_BASH_PATH_COMMANDS: dict[str, int] = {
    "ls": 1,
    "cat": 1,
    "cd": 1,
    "cp": -1,  # 最后一个参数
    "mv": -1,
    "rm": -1,
    "mkdir": -1,
    "touch": -1,
    "chmod": -1,
    "chown": -1,
}

# 文件操作工具的路径参数键名（与实际工具定义一致）
_PATH_ARG_KEYS: tuple[str, ...] = ("file_path", "path")


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

    def is_within_boundary(self, path: str | Path) -> bool:
        """检查路径是否在任一工作区边界内。

        Args:
            path: 待检查的文件/目录路径

        Returns:
            True 表示在边界内，False 表示超出边界
        """
        try:
            resolved = Path(path).resolve()
        except OSError:
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
        self, tool_name: str, tool_args: dict
    ) -> list[Path]:
        """从工具调用参数中提取涉及的文件/目录路径。

        对于文件操作工具（read/write/edit/ls/glob/grep），从参数中提取路径。
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

    def _extract_file_tool_paths(self, tool_args: dict) -> list[Path]:
        """从文件操作工具参数中提取路径。"""
        paths: list[Path] = []
        for key in _PATH_ARG_KEYS:
            value = tool_args.get(key)
            if isinstance(value, str) and value:
                paths.append(Path(value))
        return paths

    def _extract_bash_paths(self, tool_args: dict) -> list[Path]:
        """从 bash 命令中尝试提取目标路径。

        解析命令字符串，识别常见命令并提取路径参数。
        无法识别的命令返回空列表（视为边界内）。
        """
        command = tool_args.get("command") or tool_args.get("cmd")
        if not isinstance(command, str) or not command.strip():
            return []

        try:
            parts = shlex.split(command)
        except ValueError:
            logger.warning("bash 命令含无效引号，跳过边界检查: %.200s", command)
            return []

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

        args = parts[cmd_start + 1 :]
        return [Path(a) for a in args if not a.startswith("-")]

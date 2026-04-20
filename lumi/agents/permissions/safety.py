"""Bypass-immune 安全检查

即使 privileged 模式也不可跳过的安全检查。
保护敏感系统文件（shell 配置、git 配置、权限配置等）。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

# 写入类工具（只检查这些，读取类工具不阻断）
_WRITE_TOOLS: frozenset[str] = frozenset({"write", "edit"})

# 受保护的路径模式（相对于 home 目录）
_PROTECTED_HOME_PATHS: tuple[str, ...] = (
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".login",
    ".gitconfig",
)

# 受保护的路径前缀（相对于 home 目录）
_PROTECTED_HOME_PREFIXES: tuple[str, ...] = (
    ".ssh/",
    ".gnupg/",
)

# 受保护的项目相对路径
_PROTECTED_PROJECT_PATHS: tuple[str, ...] = (
    ".lumi/permissions.json",
    ".lumi/permissions.local.json",
    ".git/config",
)

# 危险 bash 命令模式（预编译正则）
_DANGEROUS_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"curl\s.*\|\s*(?:ba)?sh"), "curl 管道到 shell 执行"),
    (re.compile(r"wget\s.*\|\s*(?:ba)?sh"), "wget 管道到 shell 执行"),
)

# 写入操作匹配模式（路径占位符 {path} 由调用方填充）
# 匹配：> path, >> path, tee path, sed -i path, cp ... path, mv ... path
_WRITE_TARGET_TEMPLATES: tuple[str, ...] = (
    r">{1,2}\s*{path}",  # 重定向: > path, >> path
    r"tee\s+(?:-a\s+)?{path}",  # tee path, tee -a path
    r"sed\s+-i\s+\S+\s+{path}",  # sed -i 's/...' path
    r"cp\s+\S+\s+{path}",  # cp src path
    r"mv\s+\S+\s+{path}",  # mv src path
)

try:
    _HOME: Path | None = Path.home()
except RuntimeError:
    _HOME = None


def _is_write_target(command: str, path_pattern: str) -> bool:
    """检查受保护路径是否出现在写入操作的目标位置。"""
    for template in _WRITE_TARGET_TEMPLATES:
        pattern = template.replace("{path}", path_pattern)
        if re.search(pattern, command):
            return True
    return False


def is_bypass_immune(tool_name: str, tool_args: dict) -> tuple[bool, str]:
    """检查工具调用是否为 bypass-immune（即使 privileged 也必须审批）。

    仅检查写入类操作，读取操作不阻断。
    所有检查都是纯字符串/路径比较，不执行任何命令。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        (需要审批, 原因)。不需要审批时原因为空字符串。
    """
    if tool_name in _WRITE_TOOLS:
        return _check_file_tool(tool_args)

    if tool_name == "bash":
        return _check_bash_tool(tool_args)

    return False, ""


def _check_file_tool(tool_args: dict) -> tuple[bool, str]:
    """检查 write/edit 工具的目标路径是否受保护。"""
    file_path = tool_args.get("file_path") or tool_args.get("path")
    if not isinstance(file_path, str):
        if file_path is not None:
            return True, f"file_path 参数类型异常: {type(file_path).__name__}"
        return False, ""

    try:
        p = Path(file_path).expanduser()
    except (RuntimeError, OSError):
        return True, f"路径解析失败: {file_path}"

    # 检查 home 目录下的受保护文件
    if _HOME is not None:
        try:
            rel_to_home = p.relative_to(_HOME)
            rel_str = rel_to_home.as_posix()

            for protected in _PROTECTED_HOME_PATHS:
                if rel_str == protected:
                    return True, f"受保护文件: ~/{protected}"

            for prefix in _PROTECTED_HOME_PREFIXES:
                if rel_str.startswith(prefix):
                    return True, f"受保护目录: ~/{prefix}"
        except ValueError:
            pass  # 不在 home 目录下，继续检查

    # 检查项目相对路径（.lumi/, .git/ 等）
    path_str = p.as_posix()
    for protected in _PROTECTED_PROJECT_PATHS:
        if path_str.endswith(f"/{protected}") or path_str.endswith(
            f"/{PurePosixPath(protected)}"
        ):
            return True, f"受保护文件: {protected}"

    return False, ""


def _check_bash_tool(tool_args: dict) -> tuple[bool, str]:
    """检查 bash 命令是否包含危险模式或写入受保护路径。"""
    command = tool_args.get("command") or tool_args.get("cmd")
    if not isinstance(command, str):
        if command is not None:
            return True, f"command 参数类型异常: {type(command).__name__}"
        return False, ""

    # 检查危险命令模式
    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return True, reason

    if _HOME is None:
        return False, ""

    # 检查是否写入受保护文件
    home_str = _HOME.as_posix()

    # 检查 home 目录下的受保护文件（精确路径）
    for protected in _PROTECTED_HOME_PATHS:
        full_path = re.escape(f"{home_str}/{protected}")
        tilde_path = re.escape(f"~/{protected}")
        path_alt = f"(?:{full_path}|{tilde_path})"

        if _is_write_target(command, path_alt):
            return True, f"bash 写入受保护文件: ~/{protected}"

    # 检查 home 目录下的受保护目录（前缀匹配）
    for prefix in _PROTECTED_HOME_PREFIXES:
        full_prefix = re.escape(f"{home_str}/{prefix}")
        tilde_prefix = re.escape(f"~/{prefix}")
        path_alt = f"(?:{full_prefix}|{tilde_prefix})\\S*"

        if _is_write_target(command, path_alt):
            return True, f"bash 写入受保护目录: ~/{prefix}"

    # 检查项目相对路径（.lumi/, .git/ 等）
    for protected in _PROTECTED_PROJECT_PATHS:
        escaped = re.escape(protected)
        # 匹配绝对路径或相对路径中的受保护文件
        path_alt = f"(?:\\S*/)?{escaped}"

        if _is_write_target(command, path_alt):
            return True, f"bash 写入受保护文件: {protected}"

    return False, ""

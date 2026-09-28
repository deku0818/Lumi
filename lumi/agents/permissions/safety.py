"""Bypass-immune 安全检查

即使 privileged 模式也不可跳过的安全检查。
保护敏感系统文件（shell 配置、git 配置、权限配置等）。
"""

from __future__ import annotations

import re
from pathlib import Path

from lumi.agents.permissions.boundary import bash_write_targets
from lumi.agents.permissions.matcher import COMMAND_ARG_KEYS, extract_arg
from lumi.agents.permissions.models import PATH_ARG_KEYS
from lumi.agents.permissions.workspace import resolve_tool_path
from lumi.agents.tools.shell_syntax import DYNAMIC

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

# 受保护的项目相对路径（任意目录下同名即命中，含 ~/.lumi/ 全局层）：权限规则，以及会
# 自动执行命令的配置——hooks 在事件触发时跑 shell、MCP 配置在会话开始时拉起进程、
# config.json 的 env 注入进程环境、git 在 status 等操作时执行 config / hooks 里的命令
_PROTECTED_PROJECT_PATHS: tuple[str, ...] = (
    ".lumi/permissions.json",
    ".lumi/permissions.local.json",
    ".lumi/hooks.json",
    ".lumi/hooks.local.json",
    ".lumi/mcp_server.json",
    ".lumi/config.json",
    ".git/config",
)
_PROTECTED_PROJECT_PREFIXES: tuple[str, ...] = (".git/hooks/",)

# 危险 bash 命令模式（预编译正则）
_DANGEROUS_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"curl\s.*\|\s*(?:ba)?sh"), "curl 管道到 shell 执行"),
    (re.compile(r"wget\s.*\|\s*(?:ba)?sh"), "wget 管道到 shell 执行"),
)

try:
    _HOME: Path | None = Path.home().resolve()
except RuntimeError:
    _HOME = None


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
    file_path = extract_arg(tool_args, PATH_ARG_KEYS)
    if file_path is None:
        # 给了参数却不是字符串：fail-closed 交审批
        if any(tool_args.get(k) is not None for k in PATH_ARG_KEYS):
            return True, "file_path 参数类型异常"
        return False, ""
    reason = _target_reason(file_path)
    return bool(reason), reason


def _check_bash_tool(tool_args: dict) -> tuple[bool, str]:
    """检查 bash 命令是否包含危险模式或写入受保护路径。"""
    command = extract_arg(tool_args, COMMAND_ARG_KEYS)
    if command is None:
        if any(tool_args.get(k) is not None for k in COMMAND_ARG_KEYS):
            return True, "command 参数类型异常"
        return False, ""

    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return True, reason

    for target in bash_write_targets(command):
        if reason := _target_reason(target):
            return True, f"bash 写入{reason}"
    return False, ""


def _target_reason(target: str) -> str:
    """写入目标命中保护名单的原因（未命中为空串）。

    按工具执行同一口径归一（展开 ``~``、相对路径基于项目根、resolve ``..``）后比较，
    取两种形态：完全 resolve（跟随符号链接到真正落盘处）与只 resolve 父目录（受保护
    文件本身是符号链接时仍按原名命中）。值未知的 bash 路径（``$HOME/.bashrc``）按
    其静态尾部比较。
    """
    if DYNAMIC in target:
        return _relative_reason(target.rsplit(DYNAMIC, 1)[1].lstrip("/"))
    try:
        path = Path(target)
        candidates = (
            resolve_tool_path(path),
            resolve_tool_path(path.parent) / path.name,
        )
    except (RuntimeError, OSError):
        return f"路径解析失败: {target}"
    for candidate in candidates:
        if _HOME is not None and candidate.is_relative_to(_HOME):
            if reason := _relative_reason(candidate.relative_to(_HOME).as_posix()):
                return reason
        if reason := _project_reason(candidate.as_posix()):
            return reason
    return ""


def _relative_reason(rel: str) -> str:
    """相对家目录（或值未知前缀之后）的路径是否受保护。"""
    if rel in _PROTECTED_HOME_PATHS:
        return f"受保护文件: ~/{rel}"
    for prefix in _PROTECTED_HOME_PREFIXES:
        if rel.startswith(prefix):
            return f"受保护目录: ~/{prefix}"
    return _project_reason("/" + rel)


def _project_reason(posix: str) -> str:
    for protected in _PROTECTED_PROJECT_PATHS:
        if posix.endswith("/" + protected):
            return f"受保护文件: {protected}"
    for prefix in _PROTECTED_PROJECT_PREFIXES:
        if "/" + prefix in posix:
            return f"受保护目录: {prefix}"
    return ""

"""工具能力声明 — 只读 vs 写入的统一判定 + bash 命令拆分

每个工具分为只读或写入两类。只读工具跳过权限审批，写入工具走权限引擎。
bash 工具根据命令内容动态判断；cron 工具根据 operation 参数判断。

同时提供 split_compound_command，供模块内部判定和 permissions 权限匹配层共用。
"""

from __future__ import annotations

import re


# ── 复合命令拆分 ──

# 双字符复合分隔符（优先匹配）
_DOUBLE_SEPARATORS: frozenset[str] = frozenset({"&&", "||"})
# 单字符复合分隔符
_SINGLE_SEPARATORS: frozenset[str] = frozenset({"|", ";", "&"})


def split_compound_command(command: str) -> list[str]:
    """拆分由 &&、||、;、| 连接的复合命令，返回各子命令字符串。

    使用字符级状态机，正确处理引号（单引号、双引号）内的分隔符不拆分。
    单命令返回 [command]。

    Args:
        command: 完整的 bash 命令字符串

    Returns:
        子命令字符串列表
    """
    if not command or not command.strip():
        return [command] if command else []

    segments: list[str] = []
    current: list[str] = []
    i = 0
    n = len(command)
    in_single_quote = False
    in_double_quote = False

    while i < n:
        c = command[i]

        # 引号状态切换
        if c == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(c)
            i += 1
        elif c == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(c)
            i += 1
        elif c == "\\" and in_double_quote and i + 1 < n:
            # 双引号内的转义
            current.append(c)
            current.append(command[i + 1])
            i += 2
        elif not in_single_quote and not in_double_quote:
            # 非引号内：检查分隔符
            two = command[i : i + 2]
            if two in _DOUBLE_SEPARATORS:
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
                i += 2
            elif c in _SINGLE_SEPARATORS:
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
                i += 1
            else:
                current.append(c)
                i += 1
        else:
            current.append(c)
            i += 1

    # 末尾段
    seg = "".join(current).strip()
    if seg:
        segments.append(seg)

    if not segments:
        return [command.strip()]

    return segments


# ── 只读工具集合 ──

# 无论参数如何，始终为只读的工具
_ALWAYS_READONLY: frozenset[str] = frozenset(
    {
        "read",
        "glob",
        "grep",
        "skill",
        "agent",
        "EnterPlanMode",
        "ExitPlanMode",
        "ask",
        "todos",
    }
)

# 无论参数如何，始终为写入的工具
_ALWAYS_WRITE: frozenset[str] = frozenset({"write", "edit"})

# cron 中的只读操作
_CRON_READONLY_OPS: frozenset[str] = frozenset({"list", "runs"})


# ── 公共 API ──


def is_write_tool(tool_name: str, tool_args: dict) -> bool:
    """判断工具调用是否为写入操作

    只读工具跳过权限审批，写入工具需要经过权限引擎评估。
    未知工具默认视为写入（fail-closed）。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        True 表示写入操作，False 表示只读
    """
    if tool_name in _ALWAYS_READONLY:
        return False
    if tool_name in _ALWAYS_WRITE:
        return True
    if tool_name == "bash":
        return not is_readonly_command(tool_args.get("command", ""))
    if tool_name == "cron":
        return tool_args.get("operation", "") not in _CRON_READONLY_OPS
    # 未知工具 fail-closed
    return True


def is_read_only(tool_name: str, tool_args: dict) -> bool:
    """工具调用是否只读（is_write_tool 的反义）"""
    return not is_write_tool(tool_name, tool_args)


# ── bash 只读命令判断 ──

# 已知只读命令前缀（白名单，fail-closed）
_READONLY_PREFIXES: frozenset[str] = frozenset(
    {
        # 文件查看
        "ls",
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "bat",
        "tree",
        "exa",
        "eza",
        # 搜索
        "find",
        "grep",
        "rg",
        "ag",
        "fd",
        "fzf",
        # 文件信息
        "wc",
        "du",
        "df",
        "stat",
        "file",
        "which",
        "type",
        "whereis",
        "readlink",
        # 系统信息
        "pwd",
        "whoami",
        "hostname",
        "uname",
        "date",
        "env",
        "printenv",
        "id",
        "uptime",
        "ps",
        "top",
        "htop",
        # git 只读
        "git status",
        "git log",
        "git diff",
        "git show",
        "git branch",
        "git remote",
        "git tag",
        "git blame",
        "git stash list",
        "git rev-parse",
        "git ls-files",
        "git ls-tree",
        "git describe",
        "git shortlog",
        "git config --get",
        "git config --list",
        "git config -l",
        # 输出
        "echo",
        "printf",
        # 数据处理
        "jq",
        "yq",
        "xmllint",
        "sort",
        "uniq",
        "cut",
        "tr",
        "awk",
        "sed",  # 无 -i 时只输出
        "diff",
        "comm",
        "paste",
        "column",
        "xargs",
        # 包管理查询
        "npm list",
        "npm ls",
        "npm view",
        "pip list",
        "pip show",
        "pip freeze",
        "pip index",
        "uv pip list",
        "uv pip show",
        "cargo metadata",
        "cargo tree",
        # 项目工具（只读）
        "uv run pytest",
        "uv run ruff check",
        "uv run ruff format --check",
        "uv run mypy",
        # 网络查询
        "curl",
        "wget",
        "dig",
        "nslookup",
        "ping",
        "host",
    }
)

# 重定向操作符正则（匹配 > 或 >> 但排除 &> 和 N>&M 形式的 fd 重定向）
_REDIRECT_PATTERN = re.compile(
    r"(?<!\d)(?<!&)>{1,2}(?!&)"  # > 或 >>，但不匹配 2>&1、&>、>&
)

# sed -i 检测
_SED_INPLACE_PATTERN = re.compile(r"\bsed\s+(-\S*i|--in-place)")

# 危险 curl/wget 管道
_PIPE_TO_SHELL = re.compile(r"\|\s*(?:ba)?sh\b")


def is_readonly_command(command: str) -> bool:
    """判断 bash 命令是否只读

    使用白名单 + 危险模式检测。未识别的命令默认视为非只读（fail-closed）。

    Args:
        command: bash 命令字符串

    Returns:
        True 表示只读，False 表示可能有写操作
    """
    if not command or not command.strip():
        return True

    # 快速排除：存在重定向操作符
    if _REDIRECT_PATTERN.search(command):
        return False

    # 快速排除：sed -i（原地修改）
    if _SED_INPLACE_PATTERN.search(command):
        return False

    # 快速排除：管道到 shell
    if _PIPE_TO_SHELL.search(command):
        return False

    # 移除 fd 重定向修饰符（如 2>&1、2>/dev/null），它们不改变命令的只读性
    cleaned = re.sub(r"\d*>&\d+", "", command)
    cleaned = re.sub(r"\d+>\s*/dev/null", "", cleaned)

    # 拆分复合命令，每个子命令都必须匹配只读前缀
    sub_commands = split_compound_command(cleaned)

    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        if not _matches_readonly_prefix(sub):
            return False

    return True


def _matches_readonly_prefix(command: str) -> bool:
    """检查单条命令是否匹配只读前缀白名单"""
    for prefix in _READONLY_PREFIXES:
        if command == prefix or command.startswith(prefix + " "):
            return True
        # 支持带路径的命令（如 /usr/bin/ls）
        if "/" in prefix:
            continue
        if command.startswith(f"/usr/bin/{prefix} ") or command.startswith(
            f"/bin/{prefix} "
        ):
            return True
    return False

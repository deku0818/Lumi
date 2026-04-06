"""工具能力声明 — 统一的工具副作用元数据

每个工具声明自身的副作用类型，取代分散在多处的硬编码集合（BYPASS_TOOLS 等）。
bash 工具根据命令内容动态判断是否只读。

三层工具限制机制的 Layer 1：
- Layer 1 (本模块): 工具级元数据 — 声明每个工具的副作用类型
- Layer 2 (plan_guard): 权限引擎层 — plan mode 下拦截写操作
- Layer 3 (agent.py):  子 Agent 层 — 创建时移除写工具
"""

from __future__ import annotations

import re
from enum import Flag, auto


class ToolEffect(Flag):
    """工具副作用类型（位标志，可组合）

    Examples:
        ToolEffect.NONE                        # 纯只读
        ToolEffect.FILE_WRITE                  # 写文件
        ToolEffect.FILE_WRITE | ToolEffect.SHELL_EXEC  # 组合检查
    """

    NONE = 0
    """纯只读操作：read, glob, grep, skill"""

    FILE_WRITE = auto()
    """写文件操作：write, edit"""

    SHELL_EXEC = auto()
    """执行命令（非只读 bash）"""

    STATE_MUTATE = auto()
    """修改会话内部状态：todos, cron"""

    INTERRUPT = auto()
    """中断等待用户输入：ask, ExitPlanMode"""


# ── 静态效果声明 ──

_STATIC_EFFECTS: dict[str, ToolEffect] = {
    # 只读
    "read": ToolEffect.NONE,
    "glob": ToolEffect.NONE,
    "grep": ToolEffect.NONE,
    "skill": ToolEffect.NONE,
    "EnterPlanMode": ToolEffect.NONE,
    "agent": ToolEffect.NONE,  # 子 agent 权限由自身独立评估
    # 写文件
    "write": ToolEffect.FILE_WRITE,
    "edit": ToolEffect.FILE_WRITE,
    # 状态修改
    "cron": ToolEffect.STATE_MUTATE,
    "todos": ToolEffect.STATE_MUTATE,
    # 中断
    "ask": ToolEffect.INTERRUPT,
    "ExitPlanMode": ToolEffect.INTERRUPT,
}

# 跳过权限审批的效果集合
BYPASS_EFFECTS: ToolEffect = (
    ToolEffect.NONE | ToolEffect.INTERRUPT | ToolEffect.STATE_MUTATE
)


# ── 公共 API ──


def get_tool_effect(tool_name: str, tool_args: dict) -> ToolEffect:
    """获取工具调用的副作用类型

    Args:
        tool_name: 工具名称
        tool_args: 工具参数（bash 需要此参数判断命令内容）

    Returns:
        ToolEffect 标志。未知工具默认返回 SHELL_EXEC（fail-closed）。
    """
    if tool_name == "bash":
        return _bash_effect(tool_args)
    return _STATIC_EFFECTS.get(tool_name, ToolEffect.SHELL_EXEC)


def is_read_only(tool_name: str, tool_args: dict) -> bool:
    """工具调用是否只读"""
    return get_tool_effect(tool_name, tool_args) == ToolEffect.NONE


def should_bypass_approval(tool_name: str, tool_args: dict) -> bool:
    """是否跳过权限审批流程

    语义化替代原 BYPASS_TOOLS 硬编码集合。
    只读、中断、状态修改类工具跳过审批。
    """
    effect = get_tool_effect(tool_name, tool_args)
    # 效果全部在 BYPASS_EFFECTS 集合内 → 跳过审批
    return not (effect & ~BYPASS_EFFECTS)


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
    from lumi.agents.tools.permissions.matcher import split_compound_command

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


def _bash_effect(args: dict) -> ToolEffect:
    """bash 工具根据命令内容判断副作用"""
    command = args.get("command", "")
    if is_readonly_command(command):
        return ToolEffect.NONE
    return ToolEffect.SHELL_EXEC

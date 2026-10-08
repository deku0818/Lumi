"""工具能力声明 — 只读 vs 写入的统一判定 + bash 命令拆分

每个工具分为只读或写入两类。只读工具跳过权限审批，写入工具走权限引擎。
bash 工具根据命令内容动态判断；cron 工具根据 operation 参数判断。

同时提供 split_compound_command，供模块内部判定和 permissions 权限匹配层共用。
"""

from __future__ import annotations

import re

from lumi.agents.tools.shell_syntax import Segment, parse_command

# ── 复合命令拆分 ──


def split_compound_command(command: str) -> list[str]:
    """拆出命令里会执行的全部子命令原文（逐个参与权限规则匹配）。

    分隔符含 &&、||、;、|、&、换行与子 shell 括号；命令替换 / 进程替换里的命令也作为
    独立子命令拆出，引号、转义、注释与 heredoc 正文按 bash 规则处理（见 shell_syntax）。
    """
    texts = [seg.text for seg in parse_command(command)]
    return texts or ([command.strip()] if command.strip() else [])


def has_background_operator(command: str) -> bool:
    """检测命令中是否含 shell 后台操作符 ``&``。

    引号 / 转义 / heredoc 感知：排除 ``&&``（逻辑与）、``&>``（重定向）、``>&`` / ``<&``
    （fd 复制，如 ``2>&1``）、引号内与 ``\\&`` 转义的字面量、heredoc 正文。命令替换
    ``$(...)`` 内的 ``&`` 会命中——其中的后台符同样使进程脱离追踪，保守报告。
    """
    i = 0
    n = len(command)
    in_single_quote = False
    in_double_quote = False
    # 本行已声明、正文尚未开始的 heredoc 定界词（遇换行后按序越过各段正文）
    pending_heredocs: list[str] = []

    while i < n:
        c = command[i]
        if c == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            i += 1
        elif c == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            i += 1
        elif c == "\\" and not in_single_quote and i + 1 < n:
            i += 2  # 转义（双引号内或裸露区）：\& 是字面量
        elif in_single_quote or in_double_quote:
            i += 1
        elif c == "\n" and pending_heredocs:
            i = _skip_heredoc_bodies(command, i + 1, pending_heredocs)
            pending_heredocs = []
        elif command.startswith("$((", i):
            i = _skip_arithmetic(command, i)  # 算术扩展：& 是位与、<< 是位移
        elif command.startswith("<<<", i):
            i += 3  # herestring，无正文可跳
        elif command.startswith("<<", i):
            delimiter, i = _parse_heredoc_delimiter(command, i)
            if delimiter:
                pending_heredocs.append(delimiter)
        elif c == "&":
            nxt = command[i + 1] if i + 1 < n else ""
            prev = command[i - 1] if i > 0 else ""
            if nxt == "&":
                i += 2  # && 逻辑与
            elif nxt == ">" or prev in (">", "<", "|", ";"):
                # &> 重定向 / N>&M、<&N fd 复制 / |&（管道 stdout+stderr）/
                # case 终结符 ;& 与 ;;&——prev 为 | 或 ; 时裸 & 反而是语法错误，豁免不漏检
                i += 1
            else:
                return True
        else:
            i += 1
    return False


def _skip_arithmetic(command: str, start: int) -> int:
    """越过 ``$((...))`` 算术扩展（``start`` 指向 ``$``），返回闭合 ``))`` 之后的下标。

    括号计数配平（支持嵌套 ``( )``）；未闭合视为延伸到串尾。
    """
    depth = 2
    i = start + 3
    while i < len(command) and depth:
        if command[i] == "(":
            depth += 1
        elif command[i] == ")":
            depth -= 1
        i += 1
    return i


# heredoc 定界词：<<（可带 -）后可空格，定界词可被引号包裹或为裸词（排除 shell 元字符）
_HEREDOC_DELIMITER_PATTERN = re.compile(
    r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s<>&|;()\"']+))"
)


def _parse_heredoc_delimiter(command: str, start: int) -> tuple[str, int]:
    """解析 ``start`` 处（指向 ``<<``）的 heredoc 定界词。

    返回 (定界词, 越过定界词后的下标)；解析不出（``<<`` 悬在行尾等）返回空串并只越过
    ``<<`` 本身，交回主扫描保守处理。
    """
    match = _HEREDOC_DELIMITER_PATTERN.match(command, start)
    if not match:
        return "", start + 2
    delimiter = match.group(1) or match.group(2) or match.group(3)
    return delimiter, match.end()


def _skip_heredoc_bodies(command: str, start: int, delimiters: list[str]) -> int:
    """从 ``start``（heredoc 正文首行行首）起按序越过各段正文。

    返回最后一个终止行之后的下标。终止行允许前后空白（覆盖 ``<<-`` 的制表符缩进）；
    找不到终止行视为正文延伸到串尾。
    """
    i = start
    for delimiter in delimiters:
        while i < len(command):
            end = command.find("\n", i)
            line_end = end if end != -1 else len(command)
            line = command[i:line_end]
            i = line_end + 1
            if line.strip() == delimiter:
                break
    return i


# ── 只读工具集合 ──

# 无论参数如何，始终为只读的工具
_ALWAYS_READONLY: frozenset[str] = frozenset(
    {
        "read",
        "vision",
        "glob",
        "grep",
        "skill",
        "agent",
        "ask",
        "todos",
    }
)

# 无论参数如何，始终为写入的工具
_ALWAYS_WRITE: frozenset[str] = frozenset({"write", "edit"})

# cron 中的只读操作
_CRON_READONLY_OPS: frozenset[str] = frozenset({"list", "runs"})

# path/file_path 参数确定是本机路径的工具（write/edit 撞执行期 validate_path，
# bash 真实操作本机盘）。MCP 等外部工具不在此列：其 path 含义未知（可能是 URL、
# 库名、远端路径），且 is_write_tool 对未知工具 fail-closed 恒 True。
_LOCAL_PATH_TOOLS: frozenset[str] = frozenset({"write", "edit", "bash"})


# ── 公共 API ──


def is_file_edit_tool(tool_name: str) -> bool:
    """判断是否为文件编辑工具（write/edit），不含 bash。"""
    return tool_name in _ALWAYS_WRITE


def is_local_path_tool(tool_name: str) -> bool:
    """路径参数确定指向本机文件系统的工具（write/edit/bash）。

    用于「凭一次调用的路径去开本地目录权限」这类判断——外部工具的 path 含义未知，
    不能据此放宽本地边界。
    """
    return tool_name in _LOCAL_PATH_TOOLS


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
    if tool_name == "background_task":
        # list / status 只看本会话的任务（按会话隔离，见 bg_tasks.owned_by）
        return tool_args.get("action") not in {"list", "status"}
    # 未知工具 fail-closed
    return True


# ── bash 只读命令判断 ──

# 已知只读命令（白名单，按词匹配，fail-closed）。能执行子命令、写文件或联网的程序
# 不在此列（xargs / env / awk / sed / curl 等）——它们的「只读」取决于参数或脚本内容，
# 静态判定不可靠，交由审批模式裁决。
_READONLY_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    tuple(p.split())
    for p in (
        # 文件查看
        "ls",
        "cat",
        "head",
        "tail",
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
        "diff",
        "comm",
        "paste",
        "column",
        # 包管理查询（本地）
        "npm list",
        "npm ls",
        "pip list",
        "pip show",
        "pip freeze",
        "uv pip list",
        "uv pip show",
    )
)

# 白名单命令里能执行子命令、写文件或改系统状态的选项：带上即不算只读
_UNSAFE_OPTIONS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("find",): (
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-delete",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",
    ),
    ("rg",): ("--pre",),
    ("ag",): ("--pager",),
    ("fd",): ("-x", "--exec", "-X", "--exec-batch"),
    ("bat",): ("--pager",),
    ("tree",): ("-o",),
    ("file",): ("-C", "--compile"),
    ("date",): ("-s", "--set"),
    ("sort",): ("-o", "--output", "--compress-program"),
    ("yq",): ("-i", "--inplace"),
    ("xmllint",): ("--output",),
    ("git", "log"): ("--output",),
    ("git", "diff"): ("--output",),
    ("git", "show"): ("--output",),
}

# 操作数超过此数即有副作用的命令（uniq IN OUT 写 OUT；hostname NAME 改主机名）
_MAX_OPERANDS: dict[tuple[str, ...], int] = {("uniq",): 1, ("hostname",): 0}

# 值在执行时才确定、或本身就在执行命令的构造：不做静态只读判定
_DYNAMIC_CONSTRUCTS: tuple[str, ...] = ("$(", "`", "<(", ">(")


def is_readonly_command(command: str) -> bool:
    """判断 bash 命令是否只读（白名单，未识别的命令视为非只读）。

    每个子命令（含替换 / 子 shell 内的）都须是白名单命令、不带有副作用的选项、
    且没有写重定向（``2>/dev/null``、``2>&1`` 不算写）。
    """
    if not command.strip():
        return True
    if any(construct in command for construct in _DYNAMIC_CONSTRUCTS):
        return False
    return all(_is_readonly_segment(seg) for seg in parse_command(command))


def _is_readonly_segment(seg: Segment) -> bool:
    if seg.writes or not seg.words:
        return not seg.writes
    words = list(seg.words)
    # 支持系统目录下的绝对路径调用（如 /usr/bin/ls）；./ls 之类的本地脚本不算
    for prefix in ("/usr/bin/", "/bin/"):
        words[0] = words[0].removeprefix(prefix)
    for n in (3, 2, 1):
        head = tuple(words[:n])
        if head in _READONLY_COMMANDS:
            return not _has_side_effects(head, words[n:])
    return False


def _has_side_effects(command: tuple[str, ...], args: list[str]) -> bool:
    """白名单命令的参数是否带出副作用（查 _UNSAFE_OPTIONS / _MAX_OPERANDS）。"""
    options = _UNSAFE_OPTIONS.get(command, ())
    if any(_has_option(arg, opt) for arg in args for opt in options):
        return True
    operands = [a for a in args if not a.startswith("-")]
    return len(operands) > _MAX_OPERANDS.get(command, len(operands))


def _has_option(arg: str, option: str) -> bool:
    """``arg`` 是否带上 ``option``：长选项含 ``--opt=值``，短选项含合写（``-uo``）。"""
    if arg == option:
        return True
    if option.startswith("--") or len(option) != 2:
        return arg.startswith(option + "=")
    return arg.startswith("-") and not arg.startswith("--") and option[1] in arg[1:]

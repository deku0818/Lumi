"""工具权限控制系统 - 规则匹配器

纯函数式的规则匹配逻辑，支持工具名、命令模式、路径模式匹配。
"""

from __future__ import annotations

import re
from pathlib import Path

from lumi.agents.permissions.models import PermissionRule
from lumi.utils.logger import logger

# 需要通过命令模式匹配的工具
COMMAND_TOOLS: frozenset[str] = frozenset({"bash"})

# 需要通过路径模式匹配的工具
PATH_TOOLS: frozenset[str] = frozenset({"read", "write", "edit", "glob", "grep"})

# bash 工具中命令参数的可能键名
COMMAND_ARG_KEYS: tuple[str, ...] = ("command", "cmd")

# 路径工具中路径参数的可能键名（与实际工具定义一致）
PATH_ARG_KEYS: tuple[str, ...] = ("file_path", "path")


class RuleMatcher:
    """规则匹配器 - 纯函数式的规则匹配逻辑"""

    @staticmethod
    def parse_tool_expression(tool_expr: str) -> tuple[str, str | None]:
        """解析工具表达式，返回 (tool_name, pattern_or_none)。

        Args:
            tool_expr: 工具表达式字符串

        Returns:
            (工具名, 模式) 元组。纯工具名返回 (name, None)。

        Examples:
            >>> RuleMatcher.parse_tool_expression('read')
            ('read', None)
            >>> RuleMatcher.parse_tool_expression('bash(npm *)')
            ('bash', 'npm *')
            >>> RuleMatcher.parse_tool_expression('edit(src/**/*.py)')
            ('edit', 'src/**/*.py')
        """
        # 查找第一个 '(' 的位置
        paren_start = tool_expr.find("(")
        if paren_start == -1:
            # 纯工具名
            return (tool_expr.strip(), None)

        # 带模式的表达式：name(pattern)
        tool_name = tool_expr[:paren_start].strip()
        # 去掉末尾的 ')'
        if tool_expr.endswith(")"):
            pattern = tool_expr[paren_start + 1 : -1]
        else:
            pattern = tool_expr[paren_start + 1 :]
        return (tool_name, pattern)

    @staticmethod
    def match_command_pattern(pattern: str, command: str) -> bool:
        """匹配 bash 命令模式（支持 * 通配符）。

        将 `*` 转为正则 `.*`，其他特殊字符转义，做全匹配。
        使用 DOTALL 模式使 `.` 匹配换行符，支持多行命令（如 heredoc）。

        特殊语义：若模式以 " *" 结尾且仅含一个通配符，则尾部空格和参数
        变为可选，即 "ls *" 同时匹配 "ls" 和 "ls -la /dir"。

        Args:
            pattern: 命令模式，如 "npm *"
            command: 实际命令字符串

        Returns:
            是否匹配
        """
        try:
            # 先转义所有正则特殊字符，再将转义后的 `\\*` 替换为 `.*`
            escaped = re.escape(pattern)
            regex = escaped.replace(r"\*", ".*")

            # 若模式以 " *" 结尾且仅含一个通配符，使尾部空格和参数可选
            # 例如 "ls *" 同时匹配 "ls" 和 "ls -la /dir"
            if pattern.endswith(" *") and pattern.count("*") == 1:
                # escaped 中空格被转义为 "\ "，尾部为 "\ .*"（4字符）
                regex = regex[:-4] + "( .*)?"

            return re.fullmatch(regex, command, re.DOTALL) is not None
        except re.error:
            logger.warning("命令模式语法错误: %s", pattern)
            return False

    @staticmethod
    def match_path_pattern(pattern: str, file_path: str, project_dir: Path) -> bool:
        """匹配文件路径模式（gitignore 风格）。

        Args:
            pattern: 路径模式，如 "src/**/*.py" 或 "*.log"
            file_path: 待匹配的文件路径
            project_dir: 项目根目录

        Returns:
            是否匹配

        匹配规则:
            - `*` 匹配单层路径中的任意字符（不含 `/`）
            - `**` 匹配零或多层路径
            - 带 `/` 前缀的模式从项目根目录开始匹配
            - 不带 `/` 前缀的模式在任意目录层级匹配
        """
        try:
            # 将文件路径标准化为相对于项目根目录的路径
            path = Path(file_path)
            if path.is_absolute():
                try:
                    rel_path = path.relative_to(project_dir).as_posix()
                except ValueError:
                    # 路径不在项目目录下，无法匹配
                    return False
            else:
                rel_path = path.as_posix()

            # 判断是否为根匹配模式（带 / 前缀）
            if pattern.startswith("/"):
                anchor = True
                pattern = pattern[1:]  # 去掉前缀 /
            else:
                anchor = False

            regex_pattern = _glob_to_regex(pattern)

            if anchor:
                # 从根目录开始匹配
                return re.fullmatch(regex_pattern, rel_path) is not None
            else:
                # 在任意目录层级匹配：完整路径或路径尾部
                if re.fullmatch(regex_pattern, rel_path) is not None:
                    return True
                return re.search(r"(?:^|/)" + regex_pattern + "$", rel_path) is not None
        except re.error:
            logger.warning("路径模式语法错误: %s", pattern)
            return False

    @staticmethod
    def match_rule(rule: PermissionRule, tool_name: str, tool_args: dict) -> bool:
        """判断单条规则是否匹配给定的工具调用。

        Args:
            rule: 权限规则
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            是否匹配
        """
        parsed_name, pattern = RuleMatcher.parse_tool_expression(rule.tool)

        # 工具名不匹配，直接返回
        if parsed_name != tool_name:
            return False

        # 无模式，仅匹配工具名
        if pattern is None:
            return True

        # 有模式，根据工具类型选择匹配方式
        if tool_name in COMMAND_TOOLS:
            # bash 工具：匹配命令内容
            command = extract_arg(tool_args, COMMAND_ARG_KEYS)
            if command is None:
                return False
            return RuleMatcher.match_command_pattern(pattern, command)

        if tool_name in PATH_TOOLS:
            # 文件操作工具：匹配路径
            file_path = extract_arg(tool_args, PATH_ARG_KEYS)
            if file_path is None:
                return False
            # 使用当前工作目录作为默认项目目录
            project_dir = Path(tool_args.get("project_dir", ".")).resolve()
            return RuleMatcher.match_path_pattern(pattern, file_path, project_dir)

        # 其他工具（如 MCP 工具）带模式时，尝试将模式与第一个字符串参数匹配
        for value in tool_args.values():
            if isinstance(value, str) and RuleMatcher.match_command_pattern(
                pattern, value
            ):
                return True
        return False


def _glob_to_regex(pattern: str) -> str:
    """将 gitignore 风格的 glob 模式转为正则表达式。

    处理规则:
        - `**/` → 匹配零或多层目录（`(?:.+/)?`）
        - `/**` → 匹配零或多层路径后缀（`(?:/.*)?`）
        - `**` (独立) → 匹配任意路径（`.*`）
        - `*` → 匹配单层中任意字符（`[^/]*`）

    Args:
        pattern: gitignore 风格的 glob 模式

    Returns:
        对应的正则表达式字符串
    """
    result: list[str] = []
    i = 0
    n = len(pattern)

    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # 处理 **
                if i + 2 < n and pattern[i + 2] == "/":
                    # **/ → 匹配零或多层目录前缀
                    result.append("(?:.+/)?")
                    i += 3
                elif i > 0 and pattern[i - 1] == "/":
                    # /** → 匹配零或多层路径后缀
                    result.append("(?:/.*)?")
                    i += 2
                else:
                    # 独立的 ** → 匹配任意路径
                    result.append(".*")
                    i += 2
            else:
                # 单个 * → 匹配单层（不含 /）
                result.append("[^/]*")
                i += 1
        elif c == "?":
            result.append("[^/]")
            i += 1
        else:
            result.append(re.escape(c))
            i += 1

    return "".join(result)


def build_exact_expr(tool_name: str, tool_args: dict) -> str:
    """构造精确匹配的工具表达式。

    根据工具类型提取具体的命令或路径参数，生成如 "bash(npm test)" 的表达式。
    无参数时返回纯工具名。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        工具表达式字符串
    """
    if tool_name in COMMAND_TOOLS:
        cmd = extract_arg(tool_args, COMMAND_ARG_KEYS) or ""
        return f"{tool_name}({cmd})" if cmd else tool_name
    if tool_name in PATH_TOOLS:
        path = extract_arg(tool_args, PATH_ARG_KEYS) or ""
        return f"{tool_name}({path})" if path else tool_name
    return tool_name


def build_pattern_expr(tool_name: str, tool_args: dict) -> str:
    """构造宽泛模式的工具表达式。

    对命令工具取首个单词加 *，对路径工具取文件扩展名加 **/*。
    无参数时返回纯工具名。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        工具表达式字符串
    """
    if tool_name in COMMAND_TOOLS:
        cmd = extract_arg(tool_args, COMMAND_ARG_KEYS) or ""
        if cmd:
            words = cmd.split()
            first_word = words[0] if words else cmd
            return f"{tool_name}({first_word} *)"
        return tool_name
    if tool_name in PATH_TOOLS:
        path = extract_arg(tool_args, PATH_ARG_KEYS) or ""
        if path:
            suffix = Path(path).suffix
            if suffix:
                return f"{tool_name}(**/*{suffix})"
            return f"{tool_name}(**/*)"
        return tool_name
    return tool_name


def extract_arg(args: dict, keys: tuple[str, ...]) -> str | None:
    """从参数字典中按优先级提取字符串值。

    Args:
        args: 参数字典
        keys: 按优先级排列的键名

    Returns:
        找到的第一个字符串值，或 None
    """
    for key in keys:
        value = args.get(key)
        if isinstance(value, str):
            return value
    return None

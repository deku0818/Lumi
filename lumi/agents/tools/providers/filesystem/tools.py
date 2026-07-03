"""Filesystem 工具函数 - write / edit / glob / grep

@tool 装饰的工具函数及其 Pydantic 输入模型、grep 结果格式化辅助。
这些工具通过 get_backend() 调用 LocalFilesystemBackend 执行底层操作。

read 工具因涉及多模态 dispatch 与测试 monkeypatch，留在包 __init__。
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from lumi.agents.tools.providers.filesystem.backend import get_backend

# ============================================================================
# Pydantic Schemas
# ============================================================================


class WriteInput(BaseModel):
    file_path: str = Field(description="要创建的文件路径")
    content: str = Field(description="要写入的文件内容")


class EditInput(BaseModel):
    file_path: str = Field(description="要编辑的文件路径")
    old_string: str = Field(description="要替换的文本(必须完全匹配)")
    new_string: str = Field(description="替换后的新文本")
    replace_all: bool = Field(default=False, description="是否替换所有匹配项")


class GlobInput(BaseModel):
    pattern: str = Field(description="glob模式,如 *.py 或 **/*.json")
    path: str = Field(default=".", description="搜索起始目录")


class GrepInput(BaseModel):
    """Grep 工具输入模型"""

    pattern: str = Field(
        description="要在文件内容中搜索的正则表达式模式",
    )
    path: str | None = Field(
        default=None,
        description="搜索的文件或目录路径，默认为当前工作目录",
    )
    glob: str | None = Field(
        default=None,
        description='用于过滤文件的 glob 模式（如 "*.js"、"*.{ts,tsx}"），映射到 rg --glob',
    )
    type: str | None = Field(
        default=None,
        description="按文件类型搜索（rg --type），常见类型：js、py、rust、go、java 等。对标准文件类型比 glob 更高效",
    )
    after_context: int | None = Field(
        default=None,
        description='每个匹配行之后显示的行数（对应 rg -A）。需要 output_mode 为 "content"，否则忽略',
    )
    before_context: int | None = Field(
        default=None,
        description='每个匹配行之前显示的行数（对应 rg -B）。需要 output_mode 为 "content"，否则忽略',
    )
    context: int | None = Field(
        default=None,
        description='每个匹配行前后显示的行数（对应 rg -C）。需要 output_mode 为 "content"，否则忽略',
    )
    case_insensitive: bool = Field(
        default=False,
        description="大小写不敏感搜索（对应 rg -i）",
    )
    line_number: bool = Field(
        default=True,
        description='输出中显示行号（对应 rg -n）。需要 output_mode 为 "content"，否则忽略。默认 true',
    )
    multiline: bool = Field(
        default=False,
        description="启用多行模式，正则中的 '.' 将可匹配换行符，搜索模式可跨行匹配（对应 rg -U --multiline-dotall）。默认 false",
    )
    output_mode: Literal["content", "files_with_matches", "count"] = Field(
        default="files_with_matches",
        description='输出模式："content" 显示匹配行（支持上下文行、行号、head_limit），"files_with_matches" 显示文件路径（支持 head_limit），"count" 显示匹配计数（支持 head_limit）。默认 "files_with_matches"',
    )
    offset: int = Field(
        default=0,
        description="跳过前 N 条结果后再应用 head_limit。适用于所有输出模式。默认 0",
    )
    head_limit: int = Field(
        default=0,
        description="限制输出前 N 条结果。适用于所有输出模式：content（限制输出行数）、files_with_matches（限制文件路径数）、count（限制计数条目数）。默认 0（不限制）",
    )


# ============================================================================
# Tool Functions
# ============================================================================


@tool(args_schema=WriteInput)
async def write(file_path: str, content: str) -> str:
    """创建新文件并写入内容。父目录会自动创建。
    用法：
    - 如果文件已存在会报错,应使用edit修改。
    - 始终优先编辑代码库中已有的文件。除非明确要求，否则绝不要创建新文件。
    - 不要主动创建文档文件（*.md）或 README 文件。仅在用户明确要求时才创建文档文件。
    - 仅在用户明确要求时才使用表情符号。除非被要求，否则避免在文件中写入表情符号。"""
    backend = get_backend()
    result = await backend.write(file_path, content)
    return (
        f"错误: {result['error']}" if result["error"] else f"成功写入文件: {file_path}"
    )


@tool(args_schema=EditInput)
async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """对文件执行精确的字符串替换。

    用法：
    - 在进行编辑之前，你必须在当前对话中至少使用一次`read`工具。如果在未读取文件的情况下尝试编辑，此工具将返回错误。
    - 当基于 read 工具的输出编辑文本时，务必保留与其一致的缩进（制表符/空格），以 read 输出中"行号前缀"之后的内容为准。行号前缀的格式为：行号（右对齐，前导空格填充）+ 制表符。制表符之后的所有内容才是需要匹配的实际文件内容。绝不要在 old_string 或 new_string 中包含任何行号前缀的部分。
    - 始终优先编辑代码库中已有的文件。除非明确要求，否则绝不要创建新文件。
    - 仅在用户明确要求时才使用表情符号。除非被要求，否则避免在文件中添加表情符号。
    - 如果`old_string`在文件中不是唯一的，此次编辑将失败。请提供包含更多上下文的更大字符串以确保其唯一性，或使用`replace_all`来替换文件中所有出现的`old_string`。
    - 使用 `replace_all` 可在整个文件中批量替换或重命名字符串。例如，当你需要重命名变量时，这个参数非常有用。"""
    backend = get_backend()
    result = await backend.edit(file_path, old_string, new_string, replace_all)
    if result["error"]:
        return f"错误: {result['error']}"
    return f"成功编辑文件: {file_path} (替换了 {result['occurrences']} 处)"


@tool(args_schema=GlobInput)
async def glob(pattern: str, path: str = ".") -> str:
    """按文件名的 glob 模式递归查找文件（如 *.py、**/*.json），返回路径及大小、修改日期。
    按名字找文件用本工具；按内容找用 grep 工具。"""
    backend = get_backend()
    items = await backend.glob_info(pattern, path)

    if not items:
        return f"未找到匹配 '{pattern}' 的文件"

    lines = [f"找到 {len(items)} 个文件:"]
    for item in items:
        size_kb = item.get("size", 0) / 1024
        modified = item.get("modified_at", "")
        if modified:
            modified = modified.split("T")[0]
        lines.append(f"  {item['path']} ({size_kb:.1f}KB, {modified or '未知'})")
    return "\n".join(lines)


_GREP_DESCRIPTION = """基于 ripgrep 的文件内容搜索工具。

用法：
- 搜索任务**始终**用本工具，**绝不**在 bash 里调 `grep` / `rg` 命令——本工具已按权限与访问范围优化
- 支持完整正则语法（如 "log.*Error"、"function\\s+\\w+"）
- 用 glob 参数（如 "*.js"、"**/*.tsx"）或 type 参数（如 "js"、"py"、"rust"）过滤文件
- 输出模式："content" 显示匹配行，"files_with_matches" 只显示文件路径（默认），"count" 显示匹配计数
- 需要多轮探索的开放式搜索，改派 `agent` 工具
- 模式语法遵循 ripgrep（而非 grep）：字面花括号需转义（搜 Go 的 `interface{}` 用 `interface\\{\\}`）
- 多行匹配：默认只在单行内匹配；跨行模式（如 `struct \\{[\\s\\S]*?field`）需要 `multiline: true`
"""


def _format_grep_content(result: dict, pattern: str, line_number: bool) -> str:
    """格式化 content 模式的 grep 结果为可读字符串"""
    matches = result["matches"]
    total = result["total"]
    if not matches and result["offset"] == 0:
        return f"未找到匹配 '{pattern}' 的内容"

    lines = [f"找到 {total} 处匹配:"]
    prev_path: str | None = None
    prev_line: int | None = None

    for match in matches:
        current_path = match["path"]
        current_line = match["line"]

        # 不同文件或不连续行时添加分隔符
        if prev_path is not None and (
            current_path != prev_path
            or (prev_line is not None and current_line > prev_line + 1)
        ):
            lines.append("--")

        is_ctx = match.get("is_context", False)
        sep = "-" if is_ctx else ":"
        if line_number:
            lines.append(f"  {current_path}:{current_line}{sep} {match['text']}")
        else:
            lines.append(f"  {current_path}{sep} {match['text']}")

        prev_path = current_path
        prev_line = current_line

    if result["truncated"]:
        shown = len(matches)
        off = result["offset"]
        lines.append(f"  [已截断] 共 {total} 处匹配，显示第 {off + 1}-{off + shown} 条")
    return "\n".join(lines)


def _format_grep_files(result: list[dict], pattern: str) -> str:
    """格式化 files_with_matches 模式的 grep 结果"""
    if not result:
        return f"未找到匹配 '{pattern}' 的文件"
    lines = [f"找到 {len(result)} 个匹配文件:"]
    for item in result:
        lines.append(f"  {item['path']}")
    return "\n".join(lines)


def _format_grep_counts(result: list[dict], pattern: str) -> str:
    """格式化 count 模式的 grep 结果"""
    if not result:
        return f"未找到匹配 '{pattern}' 的内容"
    lines = [f"在 {len(result)} 个文件中找到匹配:"]
    for item in result:
        lines.append(f"  {item['path']}: {item['count']} 处匹配")
    return "\n".join(lines)


@tool(args_schema=GrepInput, description=_GREP_DESCRIPTION)
async def grep(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    type: str | None = None,
    after_context: int | None = None,
    before_context: int | None = None,
    context: int | None = None,
    case_insensitive: bool = False,
    line_number: bool = True,
    multiline: bool = False,
    output_mode: Literal[
        "content", "files_with_matches", "count"
    ] = "files_with_matches",
    offset: int = 0,
    head_limit: int = 0,
) -> str:
    """A powerful search tool built on ripgrep"""
    backend = get_backend()
    # head_limit=0 表示无限制，映射为 None 传递给后端
    effective_head_limit = None if head_limit == 0 else head_limit
    result = await backend.grep_raw(
        pattern,
        path,
        file_glob=glob,
        type_filter=type,
        after_context=after_context,
        before_context=before_context,
        context=context,
        case_insensitive=case_insensitive,
        multiline=multiline,
        output_mode=output_mode,
        offset=offset,
        head_limit=effective_head_limit,
        line_number=line_number,
    )

    # 错误字符串直接返回
    if isinstance(result, str):
        return result

    # content 模式返回分页字典
    if isinstance(result, dict):
        return _format_grep_content(result, pattern, line_number)

    # files_with_matches 模式
    if output_mode == "files_with_matches":
        return _format_grep_files(result, pattern)

    # count 模式
    if output_mode == "count":
        return _format_grep_counts(result, pattern)

    # 兜底
    if not result:
        return f"未找到匹配 '{pattern}' 的内容"
    return str(result)

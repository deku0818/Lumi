"""Filesystem工具提供者 - 提供本地文件系统操作工具

该模块提供文件读取、写入、编辑、列目录、glob查找和grep搜索功能。
所有文件操作都在授权目录范围内执行，通过路径校验确保安全。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    from lumi.agents.runtime.file_tracker import FileChangeTracker

import fitz
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import BaseModel, Field
from wcmatch import glob as wcglob

from lumi.agents.core.meta_message import meta_human_message
from lumi.agents.permissions.workspace import (
    get_authorized_directory,
    validate_path,
)
from lumi.agents.tools.providers.filesystem.media import (
    PDF_INLINE_PAGE_THRESHOLD,
    PDF_MAX_EXTRACT_SIZE,
    PDF_MAX_PAGES_PER_READ,
    SUPPORTED_IMAGE_EXTS,
    SUPPORTED_PDF_EXTS,
    MediaReadError,
    extract_pdf_pages,
    parse_pages_param,
    read_image_with_token_budget,
    validate_pdf_bytes,
)
from lumi.utils.logger import logger
from lumi.utils.read_config import get_config

# ============================================================================
# Constants
# ============================================================================

DEFAULT_READ_LIMIT = 2000
DEFAULT_CONTENT_HEAD_LIMIT = 1000
RIPGREP_TIMEOUT_SECONDS = 30
BINARY_CHECK_BYTES = 8192

# ============================================================================
# Helper Utilities
# ============================================================================


def check_empty_content(content: str) -> str | None:
    """检查文件内容是否为空"""
    if not content:
        return "文件存在但内容为空"
    if not content.strip():
        return "文件只包含空白字符"
    return None


def format_content_with_line_numbers(lines: list[str], start_line: int = 1) -> str:
    """格式化文件内容,添加行号"""
    if not lines:
        return ""
    max_line_num = start_line + len(lines) - 1
    width = len(str(max_line_num))
    return "\n".join(
        f"{start_line + i:>{width}}\t{line}" for i, line in enumerate(lines)
    )


def perform_string_replacement(
    content: str, old_string: str, new_string: str, replace_all: bool = False
) -> tuple[str, int] | str:
    """执行字符串替换,返回 (新内容, 替换次数) 或错误消息"""
    if old_string == new_string:
        return "错误: 旧字符串和新字符串相同,无需替换"
    if not old_string:
        return "错误: 要替换的字符串不能为空"

    count = content.count(old_string)
    if count == 0:
        return "错误: 未找到要替换的字符串"
    if count > 1 and not replace_all:
        return f"错误: 找到 {count} 处匹配项,但 replace_all=False。请设置 replace_all=True 以替换所有匹配项,或提供更具体的字符串以唯一匹配"

    if replace_all:
        return (content.replace(old_string, new_string), count)
    return (content.replace(old_string, new_string, 1), 1)


# ============================================================================
# Ripgrep Command Builder & Result Parsers
# ============================================================================


def _build_ripgrep_command(
    pattern: str,
    search_path: str,
    file_glob: str | None,
    type_filter: str | None,
    after_context: int | None,
    before_context: int | None,
    context: int | None,
    case_insensitive: bool,
    multiline: bool,
    output_mode: str,
) -> list[str]:
    """构建 ripgrep 命令行参数列表"""
    cmd: list[str] = ["rg"]

    # 输出模式
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("--count")
    else:
        cmd.append("--json")

    # 过滤选项
    if file_glob:
        cmd.extend(["--glob", file_glob])
    if type_filter:
        cmd.extend(["--type", type_filter])

    # 上下文行数
    if after_context is not None:
        cmd.extend(["-A", str(after_context)])
    if before_context is not None:
        cmd.extend(["-B", str(before_context)])
    if context is not None:
        cmd.extend(["-C", str(context)])

    # 匹配选项
    if case_insensitive:
        cmd.append("-i")
    else:
        # 显式指定大小写敏感，避免 ripgrep smart-case 行为
        cmd.append("--case-sensitive")
    if multiline:
        cmd.append("--multiline")

    cmd.extend(["--", pattern, search_path])
    return cmd


def _parse_ripgrep_files(stdout: str) -> list[dict[str, str]]:
    """解析 ripgrep -l 输出为文件路径列表"""
    return [{"path": line.strip()} for line in stdout.splitlines() if line.strip()]


def _parse_ripgrep_counts(stdout: str) -> list[dict[str, str | int]]:
    """解析 ripgrep --count 输出为 path:count 列表"""
    results: list[dict[str, str | int]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(":", 1)
        if len(parts) != 2:
            continue
        try:
            results.append({"path": parts[0], "count": int(parts[1])})
        except ValueError:
            continue
    return results


def _parse_ripgrep_json_match(data: dict) -> dict[str, str | int | bool] | None:
    """从 ripgrep JSON 输出的单条记录中提取匹配信息

    支持 type="match" 和 type="context" 两种记录。
    返回 None 表示该记录应被跳过。
    """
    record_type = data.get("type")
    if record_type not in ("match", "context"):
        return None

    pdata = data.get("data", {})
    file_path = pdata.get("path", {}).get("text")
    line_number = pdata.get("line_number")
    if not file_path or line_number is None:
        return None

    text = pdata.get("lines", {}).get("text", "").rstrip("\n")
    return {
        "path": file_path,
        "line": int(line_number),
        "text": text,
        "is_context": record_type == "context",
    }


def _parse_ripgrep_content(stdout: str) -> list[dict[str, str | int | bool]]:
    """解析 ripgrep --json 输出为匹配结果列表"""
    matches: list[dict[str, str | int | bool]] = []
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed = _parse_ripgrep_json_match(data)
        if parsed is not None:
            matches.append(parsed)
    return matches


# ============================================================================
# LocalFilesystemBackend - 本地文件操作后端
# ============================================================================


class LocalFilesystemBackend:
    """本地文件操作后端

    所有文件操作通过 pathlib 和本地进程执行，
    所有路径在操作前都会经过授权目录校验。
    """

    def __init__(self) -> None:
        self._tracker: FileChangeTracker | None = None

    def set_tracker(self, tracker: FileChangeTracker) -> None:
        """注册文件变更追踪器，用于 checkpoint 系统。"""
        self._tracker = tracker

    @property
    def _tracker_active(self) -> bool:
        return self._tracker is not None and self._tracker.active

    async def read(
        self, file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT
    ) -> str:
        """读取文件内容并添加行号"""
        resolved = Path(file_path).resolve()

        if not resolved.exists():
            return f"错误: 文件 '{file_path}' 不存在"

        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return f"错误: 读取文件 '{file_path}' 失败: {e}"

        empty_msg = check_empty_content(content)
        if empty_msg:
            return empty_msg

        lines = content.splitlines()
        if offset >= len(lines):
            return f"错误: 行偏移量 {offset} 超过文件长度({len(lines)} 行)"

        selected_lines = lines[offset : offset + limit]
        return format_content_with_line_numbers(selected_lines, start_line=offset + 1)

    async def write(self, file_path: str, content: str) -> dict[str, str | None]:
        """创建新文件并写入内容"""
        resolved = validate_path(file_path)

        if resolved.exists():
            return {
                "path": file_path,
                "error": f"无法写入 {file_path},因为文件已存在。请先读取文件再编辑,或写入新路径",
            }

        try:
            if self._tracker_active:
                self._tracker.record_pre_write(resolved)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return {"path": file_path, "error": None}
        except OSError as e:
            return {"path": file_path, "error": f"写入文件失败: {e}"}

    async def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, str | int | None]:
        """通过字符串替换编辑文件"""
        resolved = validate_path(file_path)

        if not resolved.exists():
            return {
                "path": file_path,
                "error": f"文件 '{file_path}' 不存在",
                "occurrences": 0,
            }

        try:
            if self._tracker_active:
                self._tracker.record_pre_edit(resolved)
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"path": file_path, "error": f"编辑文件失败: {e}", "occurrences": 0}

        result = perform_string_replacement(
            content, old_string, new_string, replace_all
        )
        if isinstance(result, str):
            return {"path": file_path, "error": result, "occurrences": 0}

        new_content, occurrences = result
        try:
            resolved.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return {"path": file_path, "error": f"编辑文件失败: {e}", "occurrences": 0}

        return {"path": file_path, "error": None, "occurrences": occurrences}

    async def glob_info(self, pattern: str, path: str | None = None) -> list[dict]:
        """使用 glob 模式递归查找文件

        Args:
            pattern: Glob 模式，如 '*.py' 或 '**/*.txt'
            path: 搜索起始目录，默认为授权工作目录

        Returns:
            匹配文件的信息列表
        """
        if path is None:
            search_path = get_authorized_directory()
        else:
            search_path = Path(path).resolve()

        if not search_path.exists() or not search_path.is_dir():
            return []

        pattern = pattern.lstrip("/")
        results: list[dict] = []

        for item in search_path.rglob("*"):
            if item.is_dir():
                continue
            try:
                rel_path = str(item.relative_to(search_path))
            except ValueError:
                continue

            if not wcglob.globmatch(
                rel_path, pattern, flags=wcglob.GLOBSTAR | wcglob.BRACE
            ):
                continue

            stat = item.stat()
            results.append(
                {
                    "path": str(item),
                    "is_dir": False,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )

        return sorted(results, key=lambda x: x["modified_at"], reverse=True)

    async def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        file_glob: str | None = None,
        type_filter: str | None = None,
        after_context: int | None = None,
        before_context: int | None = None,
        context: int | None = None,
        case_insensitive: bool = False,
        multiline: bool = False,
        output_mode: str = "content",
        offset: int = 0,
        head_limit: int | None = None,
        line_number: bool = True,
    ) -> list[dict] | dict | str:
        """在文件内容中搜索正则表达式模式

        优先使用 ripgrep，不可用时自动降级到纯 Python 实现。

        Returns:
            content 模式返回分页字典 {"matches", "total", "offset", "truncated"}；
            files_with_matches/count 模式返回 list[dict]；
            正则无效时返回错误字符串。
        """
        try:
            re.compile(pattern)
        except re.error as e:
            return f"无效的正则表达式: {e}"

        search_path = (
            str(get_authorized_directory())
            if path is None
            else str(Path(path).resolve())
        )

        results = await self._ripgrep_search(
            pattern,
            search_path,
            file_glob,
            type_filter=type_filter,
            after_context=after_context,
            before_context=before_context,
            context=context,
            case_insensitive=case_insensitive,
            multiline=multiline,
            output_mode=output_mode,
        )
        if results is None:
            results = await self._python_search(pattern, search_path, file_glob)

        # 对 content 模式应用分页截断
        if isinstance(results, list) and output_mode == "content":
            total = len(results)
            effective_limit = (
                head_limit if head_limit is not None else DEFAULT_CONTENT_HEAD_LIMIT
            )
            paginated = results[offset : offset + effective_limit]
            return {
                "matches": paginated,
                "total": total,
                "offset": offset,
                "truncated": total > offset + len(paginated),
            }

        return results

    async def _ripgrep_search(
        self,
        pattern: str,
        search_path: str,
        file_glob: str | None,
        type_filter: str | None = None,
        after_context: int | None = None,
        before_context: int | None = None,
        context: int | None = None,
        case_insensitive: bool = False,
        multiline: bool = False,
        output_mode: str = "content",
    ) -> list[dict] | None:
        """使用 ripgrep 搜索文件内容，不可用时返回 None"""
        cmd = _build_ripgrep_command(
            pattern,
            search_path,
            file_glob,
            type_filter=type_filter,
            after_context=after_context,
            before_context=before_context,
            context=context,
            case_insensitive=case_insensitive,
            multiline=multiline,
            output_mode=output_mode,
        )

        stdout = await self._run_ripgrep(cmd)
        if stdout is None:
            return None

        if output_mode == "files_with_matches":
            return _parse_ripgrep_files(stdout)
        if output_mode == "count":
            return _parse_ripgrep_counts(stdout)
        return _parse_ripgrep_content(stdout)

    async def _run_ripgrep(self, cmd: list[str]) -> str | None:
        """执行 ripgrep 子进程，返回 stdout 或 None（不可用/超时时）"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=RIPGREP_TIMEOUT_SECONDS
            )
        except FileNotFoundError:
            return None
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None

        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if proc.returncode not in (0, 1) and (
            "not found" in stderr.lower() or "command not found" in stderr.lower()
        ):
            return None

        return stdout_bytes.decode("utf-8", errors="replace")

    async def _python_search(
        self, pattern: str, search_path: str, file_glob: str | None
    ) -> list[dict[str, str | int]]:
        """纯 Python 实现的文件搜索（ripgrep 降级方案）"""
        try:
            regex = re.compile(pattern)
        except re.error:
            return []

        max_file_size = (
            get_config().config.filesystem.grep_max_file_size_mb * 1024 * 1024
        )

        search_dir = Path(search_path)
        if not search_dir.exists():
            return []

        matches: list[dict[str, str | int]] = []
        for file_path in search_dir.rglob("*"):
            if not file_path.is_file():
                continue

            if file_glob and not wcglob.globmatch(
                file_path.name, file_glob, flags=wcglob.BRACE
            ):
                continue

            try:
                if file_path.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue

            try:
                content_bytes = file_path.read_bytes()
                if b"\x00" in content_bytes[:BINARY_CHECK_BYTES]:
                    continue
                content = content_bytes.decode("utf-8", errors="ignore")
            except OSError:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "path": str(file_path),
                            "line": line_num,
                            "text": line.rstrip("\n"),
                        }
                    )

            if len(matches) >= DEFAULT_CONTENT_HEAD_LIMIT:
                break

        return matches


# ============================================================================
# Backend Factory
# ============================================================================

# 全局后端实例
_backend: LocalFilesystemBackend | None = None


def get_backend() -> LocalFilesystemBackend:
    """获取文件系统后端单例"""
    global _backend
    if _backend is None:
        _backend = LocalFilesystemBackend()
    return _backend


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


@tool
async def read(
    file_path: Annotated[
        str, Field(description="文件路径,如 config.json 或 src/main.py")
    ],
    tool_call_id: Annotated[str, InjectedToolCallId],
    offset: Annotated[int, Field(description="起始行号(从0开始,仅对文本文件有效)")] = 0,
    limit: Annotated[int, Field(description="最大读取行数(仅对文本文件有效)")] = 200,
    pages: Annotated[
        str | None,
        Field(
            description=(
                "PDF 页码范围,仅对 .pdf 文件有效。"
                "格式: '1-5' / '1,3,5' / '1-3,7,9-10'。"
                "单次最多读取 20 页。不传时小 PDF(≤10 页)整体渲染为图片,"
                "大 PDF 必须传此参数。"
            )
        ),
    ] = None,
) -> str | Command:
    """读取文件内容。文本文件返回带行号的文本;
    图片(PNG/JPG/GIF/WebP)作为 image block 注入对话;
    PDF 渲染为图片页(小 PDF 整体;大 PDF 按 pages 分段)。"""
    resolved = Path(file_path).expanduser().resolve()
    if not resolved.exists():
        return f"错误: 文件 '{file_path}' 不存在"

    ext = resolved.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTS:
        return await _read_image_command(resolved, tool_call_id)
    if ext in SUPPORTED_PDF_EXTS:
        return await _read_pdf_command(resolved, pages, tool_call_id)

    # 文本路径沿用 backend(前面多了一次 exists/扩展名判断)
    backend = get_backend()
    return await backend.read(file_path, offset, limit)


# ============================================================================
# 多模态 dispatch 辅助函数 (read 工具使用)
# ============================================================================


def _error_command(tool_call_id: str, message: str, hint: str | None = None) -> Command:
    """构造只含 ToolMessage 的错误返回。

    status="error" 让 TUI widget renderer / 统计层能与成功结果分开计数并
    以错误样式渲染。
    """
    content = f"错误: {message}"
    if hint:
        content += f"\n提示: {hint}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name="read",
                    status="error",
                )
            ]
        }
    )


async def _read_image_command(path: Path, tool_call_id: str) -> Command:
    """图片分支: ToolMessage 文本摘要 + HumanMessage 携带 image block。"""
    try:
        img = await read_image_with_token_budget(path)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[_read_image_command] 处理图片 %s 失败: %s: %s",
            path.name,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return _error_command(
            tool_call_id,
            f"处理图片 {path.name} 失败: {e}",
            "请确认文件是有效的 PNG/JPG/GIF/WebP",
        )

    summary = (
        f"已读取图片 {path.name}: {img.width}x{img.height}, "
        f"{img.media_type}, 原始 {img.original_size} 字节"
    )
    reminder = (
        f"<system-reminder>read 工具读取的图片 {path.name} 内容如下:</system-reminder>"
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=summary,
                    tool_call_id=tool_call_id,
                    name="read",
                ),
                meta_human_message(
                    [
                        {"type": "text", "text": reminder},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.media_type,
                                "data": img.base64_data,
                            },
                        },
                    ]
                ),
            ]
        }
    )


def _inspect_pdf(path: Path) -> tuple[bytes, int]:
    """读字节 + magic 校验 + 总页数。raw 返回供下游复用避免重复读盘。"""
    raw = validate_pdf_bytes(path, PDF_MAX_EXTRACT_SIZE)
    with fitz.open(stream=raw, filetype="pdf") as doc:
        page_count = doc.page_count
    return raw, page_count


async def _read_pdf_command(
    path: Path,
    pages: str | None,
    tool_call_id: str,
) -> Command:
    """PDF 分支:不传 pages 渲染全部页(≤10);传了 pages 渲染指定页。"""
    # 同步 IO 挪到 worker 线程,raw 供下游复用
    try:
        raw, total_pages = await asyncio.to_thread(_inspect_pdf, path)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        return _error_command(
            tool_call_id,
            f"无法解析 PDF {path.name}: {e}",
            "请确认文件未损坏且非加密 PDF",
        )

    if pages is not None and pages.strip():
        try:
            indices = parse_pages_param(pages, total_pages)
        except MediaReadError as e:
            return _error_command(tool_call_id, str(e), e.hint)
        return await _read_pdf_rendered_command(path, indices, tool_call_id, raw=raw)

    if total_pages > PDF_INLINE_PAGE_THRESHOLD:
        return _error_command(
            tool_call_id,
            f"PDF 共 {total_pages} 页,超过整体读取阈值 {PDF_INLINE_PAGE_THRESHOLD} 页",
            f"请使用 pages 参数分页读取,例如 pages='1-{min(total_pages, PDF_MAX_PAGES_PER_READ)}'",
        )

    return await _read_pdf_rendered_command(
        path, list(range(total_pages)), tool_call_id, raw=raw
    )


async def _read_pdf_rendered_command(
    path: Path,
    page_indices: list[int],
    tool_call_id: str,
    raw: bytes | None = None,
) -> Command:
    """PDF 渲染路径:把指定页作为 image blocks 注入。

    raw 由 _read_pdf_command 的 _inspect_pdf 复用传入,避免 extract_pdf_pages
    重新读盘/重走 magic 校验。
    """
    try:
        rendered = await extract_pdf_pages(path, page_indices, raw=raw)
    except MediaReadError as e:
        return _error_command(tool_call_id, str(e), e.hint)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[_read_pdf_rendered_command] 渲染 PDF %s 失败: %s: %s",
            path.name,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return _error_command(
            tool_call_id,
            f"渲染 PDF {path.name} 失败: {e}",
            "请确认文件未损坏",
        )

    # zip 对不等长会静默截断,断言页码不会错位
    assert len(rendered) == len(page_indices), (
        f"extract_pdf_pages 返回 {len(rendered)} 张,期望 {len(page_indices)} 张"
    )
    page_numbers = ",".join(str(i + 1) for i in page_indices)
    reminder = (
        f"<system-reminder>read 工具渲染了 PDF {path.name} 的第 "
        f"{page_numbers} 页:</system-reminder>"
    )
    blocks: list[dict] = [{"type": "text", "text": reminder}]
    for idx, img in zip(page_indices, rendered):
        blocks.append({"type": "text", "text": f"--- 第 {idx + 1} 页 ---"})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": img.base64_data,
                },
            }
        )

    summary = f"已渲染 PDF {path.name} 第 {len(rendered)} 页: {page_numbers}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=summary,
                    tool_call_id=tool_call_id,
                    name="read",
                ),
                meta_human_message(blocks),
            ]
        }
    )


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
    - 始终优先编辑代码库中已有的文件。除非明确要求，否则绝不要创建新文
    件。
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
    """使用glob模式递归查找文件。如 *.py 或 **/*.json"""
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


_GREP_DESCRIPTION = """A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use Agent tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`
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

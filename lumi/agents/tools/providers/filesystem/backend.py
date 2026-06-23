"""LocalFilesystemBackend - 本地文件操作后端

提供文件读取、写入、编辑、glob 查找和 grep 搜索的底层实现，
以及格式化/校验用的纯函数 helper。所有路径在操作前都会经过授权目录校验。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lumi.agents.runtime.file_tracker import FileChangeTracker

import wcmatch.glob

from lumi.agents.permissions.workspace import (
    get_authorized_directory,
    validate_path,
)
from lumi.agents.tools.providers.filesystem.ripgrep import (
    _build_ripgrep_command,
    _parse_ripgrep_content,
    _parse_ripgrep_counts,
    _parse_ripgrep_files,
)
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


def _glob_matches(file_path: Path, search_dir: Path, file_glob: str) -> bool:
    """对齐 ripgrep --glob 语义：不含 / 的模式匹配任意层级的文件名，
    含 / 的模式相对搜索根匹配（支持 **）。仅匹配 basename 会漏掉 '**/*.py'、
    'src/*.ts' 这类带目录的 glob。"""
    flags = wcmatch.glob.BRACE | wcmatch.glob.GLOBSTAR
    if wcmatch.glob.globmatch(file_path.name, file_glob, flags=flags):
        return True
    try:
        rel = file_path.relative_to(search_dir)
    except ValueError:
        return False
    return wcmatch.glob.globmatch(str(rel), file_glob, flags=flags)


def _reshape_python_grep(
    rows: list[dict[str, str | int]], output_mode: str
) -> list[dict[str, str | int]]:
    """将 _python_search 的 content 行重塑为 count / files_with_matches 形状，
    与 ripgrep 解析输出对齐（降级路径，否则非 content 模式会返回逐行内容字典）。"""
    if output_mode == "files_with_matches":
        seen: dict[str, None] = {}
        for r in rows:
            seen.setdefault(str(r["path"]), None)
        return [{"path": p} for p in seen]
    if output_mode == "count":
        counts: dict[str, int] = {}
        for r in rows:
            p = str(r["path"])
            counts[p] = counts.get(p, 0) + 1
        return [{"path": p, "count": n} for p, n in counts.items()]
    return rows


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


def _glob_sync(search_path: Path, pattern: str) -> list[dict]:
    """同步全树 glob 遍历（供 glob_info 经 asyncio.to_thread 调用，避免阻塞事件循环）。"""
    results: list[dict] = []
    for item in search_path.rglob("*"):
        if item.is_dir():
            continue
        try:
            rel_path = str(item.relative_to(search_path))
        except ValueError:
            continue
        if not wcmatch.glob.globmatch(
            rel_path, pattern, flags=wcmatch.glob.GLOBSTAR | wcmatch.glob.BRACE
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

        # 全树遍历对大目录（含 node_modules 等）可能很慢，放到线程执行，
        # 避免同步遍历阻塞事件循环、卡住 WS/agent 流。
        return await asyncio.to_thread(_glob_sync, search_path, pattern.lstrip("/"))

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
            rows = await self._python_search(
                pattern, search_path, file_glob, case_insensitive=case_insensitive
            )
            results = _reshape_python_grep(rows, output_mode)

        # content 模式：返回带分页元信息的 dict
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

        # files_with_matches / count：按 offset/head_limit 截断列表（与工具文档一致；
        # head_limit=None 即不限，保持返回 list 形状不变）
        if isinstance(results, list) and (offset or head_limit is not None):
            end = offset + head_limit if head_limit is not None else None
            return results[offset:end]

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
        except TimeoutError:
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
        self,
        pattern: str,
        search_path: str,
        file_glob: str | None,
        case_insensitive: bool = False,
    ) -> list[dict[str, str | int]]:
        """纯 Python 实现的文件搜索（ripgrep 降级方案）"""
        try:
            regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
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

            if file_glob and not _glob_matches(file_path, search_dir, file_glob):
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

"""LocalFilesystemBackend - 本地文件操作后端

提供文件读取、写入、编辑的底层实现，
以及格式化/校验用的纯函数 helper。所有路径在操作前都会经过授权目录校验。
"""

from __future__ import annotations

import asyncio
import codecs
import locale

from lumi.agents.permissions.workspace import (
    resolve_tool_path,
    validate_path,
)

# ============================================================================
# Constants
# ============================================================================

DEFAULT_READ_LIMIT = 2000
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


def split_lines(content: str) -> list[str]:
    r"""按 \n 切行（兼容 \r\n）。不用 str.splitlines()：它还在 \f、\v、\x85、U+2028 等处
    断行，行号就与 rg / 编辑器对不上，按 read 视图写的 old_string 也匹配不到。"""
    lines = [line.removesuffix("\r") for line in content.split("\n")]
    if lines[-1] == "":
        lines.pop()
    return lines


def perform_string_replacement(
    content: str, old_string: str, new_string: str, replace_all: bool = False
) -> tuple[str, int] | str:
    """执行字符串替换,返回 (新内容, 替换次数) 或错误消息"""
    if old_string == new_string:
        return "旧字符串和新字符串相同,无需替换"
    if not old_string:
        return "要替换的字符串不能为空"

    count = content.count(old_string)
    if count == 0:
        return "未找到要替换的字符串"
    if count > 1 and not replace_all:
        return f"找到 {count} 处匹配项,但 replace_all=False。请设置 replace_all=True 以替换所有匹配项,或提供更具体的字符串以唯一匹配"

    if replace_all:
        return (content.replace(old_string, new_string), count)
    return (content.replace(old_string, new_string, 1), 1)


# ============================================================================
# LocalFilesystemBackend - 本地文件操作后端
# ============================================================================


class LocalFilesystemBackend:
    """本地文件操作后端

    所有文件操作通过 pathlib 和本地进程执行，
    所有路径在操作前都会经过授权目录校验。
    """

    async def read(
        self, file_path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT
    ) -> str:
        """读取文件内容并添加行号（读盘与解码在线程里做：网关所有会话共用一个事件循环）"""
        return await asyncio.to_thread(self._read_sync, file_path, offset, limit)

    def _read_sync(self, file_path: str, offset: int, limit: int) -> str:
        resolved = resolve_tool_path(file_path)

        if not resolved.exists():
            return f"错误: 文件 '{file_path}' 不存在"

        note = ""
        try:
            raw = resolved.read_bytes()
        except OSError as e:
            return f"错误: 读取文件 '{file_path}' 失败: {e}"

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            # 中文 Windows 上大量 txt/csv/bat 是 GBK：直接报错等于读不了。回落到系统
            # 本地编码并明说，模型才不会把可能的乱码当原文对待（edit/write 保持严格
            # UTF-8，不做这个回落——否则一次编辑就把整个文件的编码悄悄换掉了）
            fallback = locale.getpreferredencoding(False)
            # POSIX 上 preferred encoding 本就是 UTF-8：同一份字节配 errors="replace"
            # 再解一遍必然“成功”，二进制文件会变成一屏 � 而不是一句解码失败
            if codecs.lookup(fallback).name == "utf-8":
                return f"错误: 读取文件 '{file_path}' 失败: {e}"
            # GBK 之类的单/双字节编码几乎吃得下任意字节，二进制文件同样会“解码成功”；
            # 用与 rg 同一条判据（前 8KB 含 NUL 即二进制）在解码前挡掉
            if b"\x00" in raw[:BINARY_CHECK_BYTES]:
                return f"错误: 文件 '{file_path}' 不是文本文件"
            content = raw.decode(fallback, errors="replace")
            note = f"（非 UTF-8 文件，已按 {fallback} 解码）\n"

        empty_msg = check_empty_content(content)
        if empty_msg:
            return empty_msg

        lines = split_lines(content)
        if offset >= len(lines):
            return f"错误: 行偏移量 {offset} 超过文件长度({len(lines)} 行)"

        selected_lines = lines[offset : offset + limit]
        # 提示放在带行号的正文之前拼接，不混进 content——否则它会占掉第 1 行，
        # 后面所有行号整体偏移 1
        return note + format_content_with_line_numbers(
            selected_lines, start_line=offset + 1
        )

    async def write(self, file_path: str, content: str) -> dict[str, str | None]:
        """创建新文件并写入内容"""
        resolved = validate_path(file_path)

        if resolved.exists():
            return {
                "path": file_path,
                "error": f"无法写入 {file_path},因为文件已存在。请先读取文件再编辑,或写入新路径",
            }

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            # newline=""：模型写的 \n 原样落盘，不被 os.linesep 悄悄译成 CRLF（同一份
            # 内容在三个平台上生成同样的字节）
            resolved.write_text(content, encoding="utf-8", newline="")
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
            # 从字节解码而非 read_text：后者会做通用换行翻译（CRLF→\n），原文件的行尾
            # 就此丢失（read_text 的 newline 参数要 3.13 才有，本项目下限 3.12）
            raw = resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"path": file_path, "error": f"编辑文件失败: {e}", "occurrences": 0}

        # 在原文上替换，只把待匹配串对齐到文件的行尾——不归一全文：归一再整体还原会把
        # 没被改到的行也一起改写（混合行尾的文件——CSV 引号内的 CRLF、Windows/Unix
        # 工具交替动过的文件——一次小改动就变成全文件 diff）。old_string 里用 \n 还是
        # \r\n 都能命中：先压成 \n 再按需展开
        # 只在 CRLF 形态确实出现在文件里时才换：混合行尾的文件里，LF 段落的多行编辑
        # 按原样才能命中
        if "\r\n" in raw:
            crlf_old = old_string.replace("\r\n", "\n").replace("\n", "\r\n")
            if crlf_old in raw:
                old_string = crlf_old
                new_string = new_string.replace("\r\n", "\n").replace("\n", "\r\n")

        result = perform_string_replacement(raw, old_string, new_string, replace_all)
        if isinstance(result, str):
            return {"path": file_path, "error": result, "occurrences": 0}

        new_content, occurrences = result
        try:
            # newline=""：默认的 newline=None 在写侧会把 \n 译成 os.linesep，
            # Windows 上改一行 LF 文件会整份变 CRLF
            resolved.write_text(new_content, encoding="utf-8", newline="")
        except OSError as e:
            return {"path": file_path, "error": f"编辑文件失败: {e}", "occurrences": 0}

        return {"path": file_path, "error": None, "occurrences": occurrences}


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

"""Ripgrep 命令构建与输出解析

把 ripgrep 命令行参数构建和各输出模式（files/count/json content）的
解析逻辑独立出来，供 LocalFilesystemBackend 复用。
"""

from __future__ import annotations

import json


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

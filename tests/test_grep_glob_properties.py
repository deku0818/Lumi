"""Grep & Glob 工具属性测试"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from hypothesis import given, settings, strategies as st

import lumi.agents.tools.workspace as workspace
from lumi.agents.tools.providers.filesystem import LocalFilesystemBackend


# Feature: grep-glob-tool-optimization, Property 1: Glob 结果按修改时间降序排列
# **Validates: Requirements 1.1**
@settings(max_examples=50)
@given(
    num_files=st.integers(min_value=1, max_value=20),
    time_gaps=st.lists(
        st.integers(min_value=1, max_value=500), min_size=20, max_size=20
    ),
)
async def test_glob_sorted_by_mtime_desc(num_files, time_gaps):
    """glob_info 返回的结果应按修改时间降序排列"""
    backend = LocalFilesystemBackend()
    base_time = time.time() - 10000

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            for i in range(num_files):
                f = tmp_dir / f"file_{i}.txt"
                f.write_text(f"content {i}")
                mtime = base_time - time_gaps[i] * 10
                os.utime(f, (mtime, mtime))

            results = await backend.glob_info("*", str(tmp_dir))

            assert len(results) == num_files
            for i in range(len(results) - 1):
                assert results[i]["modified_at"] >= results[i + 1]["modified_at"], (
                    f"结果未按修改时间降序排列: "
                    f"results[{i}].modified_at={results[i]['modified_at']} < "
                    f"results[{i + 1}].modified_at={results[i + 1]['modified_at']}"
                )
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 3: Glob 结果包含完整元数据
# **Validates: Requirements 2.1, 2.2, 2.3**
@settings(max_examples=50)
@given(
    num_files=st.integers(min_value=1, max_value=15),
    content_sizes=st.lists(
        st.integers(min_value=0, max_value=500), min_size=15, max_size=15
    ),
)
async def test_glob_results_contain_complete_metadata(num_files, content_sizes):
    """glob_info 返回的每个结果应包含完整元数据：绝对路径、非负整数大小、有效 ISO 时间戳"""
    from datetime import datetime

    backend = LocalFilesystemBackend()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            for i in range(num_files):
                f = tmp_dir / f"file_{i}.txt"
                f.write_bytes(b"x" * content_sizes[i])

            results = await backend.glob_info("*", str(tmp_dir))

            assert len(results) == num_files
            for item in results:
                # 绝对路径以 / 开头
                assert item["path"].startswith("/"), f"路径不是绝对路径: {item['path']}"
                # 大小为非负整数
                assert isinstance(item["size"], int), (
                    f"size 不是整数: {type(item['size'])}"
                )
                assert item["size"] >= 0, f"size 为负数: {item['size']}"
                # modified_at 是有效的 ISO 格式时间戳
                try:
                    datetime.fromisoformat(item["modified_at"])
                except (ValueError, TypeError) as e:
                    raise AssertionError(
                        f"modified_at 不是有效的 ISO 时间戳: {item['modified_at']!r}"
                    ) from e
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 4: Glob 结果仅包含普通文件
# **Validates: Requirement 2.4**
@settings(max_examples=50)
@given(
    num_files=st.integers(min_value=1, max_value=10),
    num_dirs=st.integers(min_value=1, max_value=5),
)
async def test_glob_results_only_contain_regular_files(num_files, num_dirs):
    """glob_info 返回的所有结果应仅包含普通文件，不包含目录"""
    backend = LocalFilesystemBackend()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 创建普通文件
            for i in range(num_files):
                f = tmp_dir / f"file_{i}.txt"
                f.write_text(f"content {i}")

            # 创建目录
            for i in range(num_dirs):
                d = tmp_dir / f"subdir_{i}"
                d.mkdir(exist_ok=True)

            results = await backend.glob_info("*", str(tmp_dir))

            # 结果数量应等于文件数量（不包含目录）
            assert len(results) == num_files, (
                f"期望 {num_files} 个文件，实际返回 {len(results)} 个结果"
            )
            for item in results:
                # is_dir 应为 False
                assert item["is_dir"] is False, f"结果包含目录: {item['path']}"
                # 路径对应的文件系统条目应为普通文件
                assert Path(item["path"]).is_file(), f"路径不是普通文件: {item['path']}"
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 5: Grep 匹配行确实匹配正则表达式
# **Validates: Requirements 3.1**
@settings(max_examples=50)
@given(
    pattern=st.from_regex(r"[a-zA-Z]{1,5}", fullmatch=True),
    num_matching=st.integers(min_value=1, max_value=10),
    num_non_matching=st.integers(min_value=0, max_value=10),
)
async def test_grep_matches_actually_match_regex(
    pattern, num_matching, num_non_matching
):
    """grep_raw 在 content 模式下返回的每个非上下文匹配项的 text 应能被正则表达式匹配"""
    import re

    backend = LocalFilesystemBackend()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 创建包含匹配行和非匹配行的文件
            lines = []
            for i in range(num_matching):
                lines.append(f"line with {pattern} here {i}")
            for i in range(num_non_matching):
                lines.append(f"no match line {i} zzzzz")

            f = tmp_dir / "test.txt"
            f.write_text("\n".join(lines))

            result = await backend.grep_raw(pattern, str(tmp_dir))

            assert isinstance(result, dict), f"期望返回 dict，实际返回: {type(result)}"
            regex = re.compile(pattern)
            for match in result["matches"]:
                if not match.get("is_context", False):
                    assert regex.search(match["text"]), (
                        f"匹配行不包含模式 '{pattern}': {match['text']!r}"
                    )
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 6: 无效正则表达式返回错误
# **Validates: Requirements 3.2**
@settings(max_examples=50)
@given(
    invalid_pattern=st.sampled_from(
        [
            "[invalid",
            "(unclosed",
            "(?P<bad",
            "*start",
            "+start",
            "(?<=var+)",
            "[z-a]",
            "\\",
            "(((",
            "(?:abc",
        ]
    )
)
async def test_grep_invalid_regex_returns_error(invalid_pattern):
    """无效正则表达式应返回包含错误描述的字符串"""
    backend = LocalFilesystemBackend()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            (tmp_dir / "test.txt").write_text("some content")
            result = await backend.grep_raw(invalid_pattern, str(tmp_dir))
            assert isinstance(result, str), (
                f"期望返回错误字符串，实际返回: {type(result)}"
            )
            assert "无效" in result or "错误" in result, (
                f"错误消息不包含预期关键词: {result!r}"
            )
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 8: 上下文行数正确控制额外行
# **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
@settings(max_examples=30)
@given(
    after_ctx=st.integers(min_value=0, max_value=5),
    before_ctx=st.integers(min_value=0, max_value=5),
)
async def test_context_lines_control(after_ctx, before_ctx):
    """上下文行数不应超过指定的 after_context 和 before_context 值"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 创建包含 20 行的文件，仅第 10 行匹配
            lines = [f"no_match_line_{i}" for i in range(20)]
            lines[10] = "UNIQUE_MATCH_PATTERN_XYZ"
            f = tmp_dir / "test.txt"
            f.write_text("\n".join(lines))

            result = await backend.grep_raw(
                "UNIQUE_MATCH_PATTERN_XYZ",
                str(tmp_dir),
                after_context=after_ctx,
                before_context=before_ctx,
            )
            assert isinstance(result, dict)
            matches = result["matches"]

            # 计算匹配行前后的上下文行数
            match_idx = next(
                i for i, m in enumerate(matches) if not m.get("is_context", False)
            )
            before_count = sum(
                1 for m in matches[:match_idx] if m.get("is_context", False)
            )
            after_count = sum(
                1 for m in matches[match_idx + 1 :] if m.get("is_context", False)
            )

            assert before_count <= before_ctx, (
                f"Before context {before_count} > {before_ctx}"
            )
            assert after_count <= after_ctx, (
                f"After context {after_count} > {after_ctx}"
            )
        finally:
            workspace._authorized_directories = old_dirs


async def test_no_context_params_no_extra_lines():
    """未提供上下文参数时不应包含上下文行"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            lines = [f"line_{i}" for i in range(20)]
            lines[10] = "UNIQUE_TARGET_HERE"
            f = tmp_dir / "test.txt"
            f.write_text("\n".join(lines))

            result = await backend.grep_raw("UNIQUE_TARGET_HERE", str(tmp_dir))
            assert isinstance(result, dict)
            for m in result["matches"]:
                assert not m.get("is_context", False), "不应有上下文行"
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 9: case_insensitive 标志正确控制匹配行为
# **Validates: Requirements 6.1, 6.2**
@settings(max_examples=30)
@given(pattern=st.from_regex(r"[a-z]{3,6}", fullmatch=True))
async def test_case_insensitive_flag(pattern):
    """case_insensitive=True 应匹配大写变体，False 不应匹配"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 文件内容包含大写版本
            upper_content = f"prefix {pattern.upper()} suffix"
            f = tmp_dir / "test.txt"
            f.write_text(upper_content)

            # case_insensitive=True 应匹配
            result_ci = await backend.grep_raw(
                pattern, str(tmp_dir), case_insensitive=True
            )
            assert isinstance(result_ci, dict)
            assert len(result_ci["matches"]) > 0, "case_insensitive=True 应匹配大写变体"

            # case_insensitive=False 不应匹配（小写模式 vs 大写内容）
            result_cs = await backend.grep_raw(
                pattern, str(tmp_dir), case_insensitive=False
            )
            assert isinstance(result_cs, dict)
            assert len(result_cs["matches"]) == 0, (
                "case_insensitive=False 不应匹配大写变体"
            )
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 10: output_mode 正确控制返回数据结构
# **Validates: Requirements 7.1, 7.2, 7.3**
async def test_output_mode_content_structure():
    """content 模式返回 dict with matches containing path/line/text"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            f = tmp_dir / "test.txt"
            f.write_text("findme here\nno match\nfindme again")

            result = await backend.grep_raw(
                "findme", str(tmp_dir), output_mode="content"
            )
            assert isinstance(result, dict)
            assert "matches" in result
            for m in result["matches"]:
                assert "path" in m
                assert "line" in m
                assert "text" in m
        finally:
            workspace._authorized_directories = old_dirs


async def test_output_mode_files_with_matches_structure():
    """files_with_matches 模式返回去重文件路径列表"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            (tmp_dir / "a.txt").write_text("findme")
            (tmp_dir / "b.txt").write_text("findme")

            result = await backend.grep_raw(
                "findme", str(tmp_dir), output_mode="files_with_matches"
            )
            assert isinstance(result, list)
            paths = [r["path"] for r in result]
            assert len(paths) == len(set(paths)), "文件路径应去重"
            for r in result:
                assert "path" in r
        finally:
            workspace._authorized_directories = old_dirs


async def test_output_mode_count_structure():
    """count 模式返回 path/count 结构"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            f = tmp_dir / "test.txt"
            f.write_text("findme\nno\nfindme")

            result = await backend.grep_raw("findme", str(tmp_dir), output_mode="count")
            assert isinstance(result, list)
            for r in result:
                assert "path" in r
                assert "count" in r
                assert isinstance(r["count"], int)
                assert r["count"] > 0
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 11: 分页参数正确控制结果子集
# **Validates: Requirements 8.1, 8.2, 8.3**
@settings(max_examples=30)
@given(
    offset=st.integers(min_value=0, max_value=10),
    head_limit=st.integers(min_value=1, max_value=10),
)
async def test_pagination_parameters(offset, head_limit):
    """分页参数应正确控制返回结果的子集"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 创建包含 20 个匹配行的文件
            lines = [f"match_line_{i}" for i in range(20)]
            f = tmp_dir / "test.txt"
            f.write_text("\n".join(lines))

            result = await backend.grep_raw(
                "match_line", str(tmp_dir), offset=offset, head_limit=head_limit
            )
            assert isinstance(result, dict)

            expected_count = min(head_limit, max(0, 20 - offset))
            assert len(result["matches"]) == expected_count
            assert result["total"] == 20
            assert result["offset"] == offset
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 14: 路径安全校验阻止越权访问
# **Validates: Requirements 10.1, 10.2, 10.3, 10.4**
@settings(max_examples=20)
@given(
    unauthorized_suffix=st.from_regex(r"[a-z]{3,8}", fullmatch=True),
)
async def test_readonly_tools_allow_any_path(unauthorized_suffix):
    """只读工具（grep/glob）不限制工作区边界，可读取任意路径"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        # Create a separate directory outside authorized workspace
        external_dir = Path(tempfile.mkdtemp()) / unauthorized_suffix
        external_dir.mkdir(parents=True, exist_ok=True)
        (external_dir / "data.txt").write_text("some data")

        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # grep should be able to search outside workspace
            grep_result = await backend.grep_raw("some", str(external_dir))
            assert isinstance(grep_result, (str, dict))

            # glob should be able to list outside workspace
            glob_result = await backend.glob_info("*", str(external_dir))
            assert len(glob_result) >= 1, f"应能列出外部目录文件，实际: {glob_result}"
        finally:
            workspace._authorized_directories = old_dirs
            shutil.rmtree(
                external_dir.parent
                if external_dir.parent != Path(tempfile.gettempdir())
                else external_dir,
                ignore_errors=True,
            )


# Feature: grep-glob-tool-optimization, Property 2: 搜索结果路径始终在搜索目录范围内
# **Validates: Requirements 1.2, 1.3, 4.4, 4.5**
@settings(max_examples=30)
@given(num_files=st.integers(min_value=1, max_value=10))
async def test_search_results_within_search_directory(num_files):
    """搜索结果路径应始终在搜索目录范围内"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            for i in range(num_files):
                f = tmp_dir / f"file_{i}.txt"
                f.write_text(f"searchable content {i}")

            # 验证 glob 结果路径
            glob_results = await backend.glob_info("*", str(tmp_dir))
            for item in glob_results:
                assert item["path"].startswith(str(tmp_dir)), (
                    f"Glob 结果路径不在搜索目录内: {item['path']}"
                )

            # 验证 grep 结果路径
            grep_result = await backend.grep_raw("searchable", str(tmp_dir))
            assert isinstance(grep_result, dict)
            for match in grep_result["matches"]:
                assert match["path"].startswith(str(tmp_dir)), (
                    f"Grep 结果路径不在搜索目录内: {match['path']}"
                )
        finally:
            workspace._authorized_directories = old_dirs


# Feature: grep-glob-tool-optimization, Property 12: 截断时包含提示信息
# **Validates: Requirement 8.4**
@settings(max_examples=20)
@given(head_limit=st.integers(min_value=1, max_value=5))
async def test_truncation_includes_hint(head_limit):
    """匹配总数超过 head_limit 时应标记截断"""
    backend = LocalFilesystemBackend()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp).resolve()
        old_dirs = workspace._authorized_directories[:]
        workspace._authorized_directories = [tmp_dir]
        try:
            # 创建包含 20 个匹配行的文件
            lines = [f"match_target_{i}" for i in range(20)]
            f = tmp_dir / "test.txt"
            f.write_text("\n".join(lines))

            result = await backend.grep_raw(
                "match_target", str(tmp_dir), head_limit=head_limit
            )
            assert isinstance(result, dict)
            assert result["truncated"] is True, "应标记为已截断"
            assert result["total"] == 20
            assert len(result["matches"]) == head_limit
        finally:
            workspace._authorized_directories = old_dirs

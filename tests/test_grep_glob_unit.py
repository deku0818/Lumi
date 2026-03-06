"""Grep & Glob 工具单元测试 - 边界情况和集成点"""

from __future__ import annotations

from lumi.agents.tools.providers.filesystem import LocalFilesystemBackend


class TestGlobEdgeCases:
    """Glob 工具边界情况测试"""

    async def test_glob_empty_result_message(self, authorized_tmp_dir):
        """空 glob 结果应返回空列表"""
        backend = LocalFilesystemBackend()
        result = await backend.glob_info("*.nonexistent", str(authorized_tmp_dir))
        assert result == []

    async def test_glob_path_not_exists(self, authorized_tmp_dir):
        """路径不存在时应返回空列表"""
        backend = LocalFilesystemBackend()
        result = await backend.glob_info("*", str(authorized_tmp_dir / "no_such_dir"))
        assert result == []

    async def test_glob_sorted_by_mtime_desc(self, authorized_tmp_dir):
        """结果应按修改时间降序排列"""
        import os
        import time

        backend = LocalFilesystemBackend()
        base = time.time()

        f1 = authorized_tmp_dir / "old.txt"
        f1.write_text("old")
        os.utime(f1, (base - 1000, base - 1000))

        f2 = authorized_tmp_dir / "new.txt"
        f2.write_text("new")
        os.utime(f2, (base, base))

        result = await backend.glob_info("*.txt", str(authorized_tmp_dir))
        assert len(result) == 2
        assert "new.txt" in result[0]["path"]
        assert "old.txt" in result[1]["path"]

    async def test_glob_metadata_fields(self, authorized_tmp_dir):
        """结果应包含完整元数据字段"""
        f = authorized_tmp_dir / "meta.txt"
        f.write_text("hello world")

        backend = LocalFilesystemBackend()
        result = await backend.glob_info("*.txt", str(authorized_tmp_dir))
        assert len(result) == 1
        item = result[0]
        assert "path" in item
        assert "size" in item
        assert "modified_at" in item
        assert "is_dir" in item
        assert item["is_dir"] is False
        assert item["size"] > 0


class TestGrepEdgeCases:
    """Grep 工具边界情况测试"""

    async def test_grep_zero_matches(self, authorized_tmp_dir):
        """零匹配应返回空 matches 列表"""
        (authorized_tmp_dir / "test.txt").write_text("hello world")
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw("zzz_no_match", str(authorized_tmp_dir))
        assert isinstance(result, dict)
        assert result["matches"] == []
        assert result["total"] == 0
        assert result["truncated"] is False

    async def test_grep_invalid_regex(self, authorized_tmp_dir):
        """无效正则应返回错误字符串"""
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw("[invalid", str(authorized_tmp_dir))
        assert isinstance(result, str)
        assert "无效" in result

    async def test_grep_offset_out_of_range(self, authorized_tmp_dir):
        """offset 超出结果范围应返回空 matches"""
        (authorized_tmp_dir / "test.txt").write_text("match_line\nmatch_line")
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw(
            "match_line", str(authorized_tmp_dir), offset=100
        )
        assert isinstance(result, dict)
        assert result["matches"] == []
        assert result["total"] == 2
        assert result["offset"] == 100

    async def test_grep_path_not_exists(self, authorized_tmp_dir):
        """搜索不存在的路径（在授权目录内）"""
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw("test", str(authorized_tmp_dir / "no_such_dir"))
        # ripgrep 或 python 降级均应返回空结果
        if isinstance(result, dict):
            assert result["matches"] == []

    async def test_grep_case_insensitive(self, authorized_tmp_dir):
        """大小写不敏感搜索"""
        (authorized_tmp_dir / "test.txt").write_text("Hello World")
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw(
            "hello", str(authorized_tmp_dir), case_insensitive=True
        )
        assert isinstance(result, dict)
        assert len(result["matches"]) == 1

    async def test_grep_files_with_matches_mode(self, authorized_tmp_dir):
        """files_with_matches 模式返回文件路径列表"""
        (authorized_tmp_dir / "a.py").write_text("target\ntarget")
        (authorized_tmp_dir / "b.py").write_text("target")
        (authorized_tmp_dir / "c.txt").write_text("no match")
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw(
            "target", str(authorized_tmp_dir), output_mode="files_with_matches"
        )
        assert isinstance(result, list)
        paths = [r["path"] for r in result]
        assert len(paths) == 2  # a.py and b.py

    async def test_grep_count_mode(self, authorized_tmp_dir):
        """count 模式返回匹配计数"""
        (authorized_tmp_dir / "test.txt").write_text("foo\nbar\nfoo\nfoo")
        backend = LocalFilesystemBackend()
        result = await backend.grep_raw(
            "foo", str(authorized_tmp_dir), output_mode="count"
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["count"] == 3


class TestGrepTruncation:
    """截断提示测试"""

    async def test_truncation_hint_format(self, authorized_tmp_dir):
        """截断时工具函数输出应包含截断提示"""
        from lumi.agents.tools.providers.filesystem import grep

        lines = [f"match_target_{i}" for i in range(20)]
        (authorized_tmp_dir / "test.txt").write_text("\n".join(lines))

        result = await grep.ainvoke(
            {
                "pattern": "match_target",
                "path": str(authorized_tmp_dir),
                "output_mode": "content",
                "head_limit": 5,
            }
        )
        assert "[已截断]" in result
        assert "共 20 处匹配" in result

    async def test_no_truncation_when_within_limit(self, authorized_tmp_dir):
        """未超出限制时不应有截断提示"""
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "test.txt").write_text("match_here\nmatch_here")

        result = await grep.ainvoke(
            {
                "pattern": "match_here",
                "path": str(authorized_tmp_dir),
                "output_mode": "content",
            }
        )
        assert "[已截断]" not in result


class TestGlobToolFormat:
    """Glob 工具函数格式化测试"""

    async def test_glob_tool_empty_result(self, authorized_tmp_dir):
        """空结果应返回说明消息"""
        from lumi.agents.tools.providers.filesystem import glob

        result = await glob.ainvoke(
            {
                "pattern": "*.nonexistent",
                "path": str(authorized_tmp_dir),
            }
        )
        assert "未找到" in result

    async def test_glob_tool_with_metadata(self, authorized_tmp_dir):
        """结果应包含文件大小和修改日期"""
        from lumi.agents.tools.providers.filesystem import glob

        (authorized_tmp_dir / "test.py").write_text("print('hello')")

        result = await glob.ainvoke(
            {
                "pattern": "*.py",
                "path": str(authorized_tmp_dir),
            }
        )
        assert "找到 1 个文件" in result
        assert "KB" in result


class TestGrepToolFormat:
    """Grep 工具函数格式化测试"""

    async def test_grep_tool_no_match_message(self, authorized_tmp_dir):
        """零匹配应返回未找到消息"""
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "test.txt").write_text("hello")
        # 默认 files_with_matches 模式
        result = await grep.ainvoke(
            {
                "pattern": "zzz_no_match",
                "path": str(authorized_tmp_dir),
            }
        )
        assert "未找到" in result

    async def test_grep_tool_no_match_content_mode(self, authorized_tmp_dir):
        """content 模式零匹配应返回未找到消息"""
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "test.txt").write_text("hello")
        result = await grep.ainvoke(
            {
                "pattern": "zzz_no_match",
                "path": str(authorized_tmp_dir),
                "output_mode": "content",
            }
        )
        assert "未找到" in result

    async def test_grep_tool_files_with_matches_format(self, authorized_tmp_dir):
        """files_with_matches 模式格式化"""
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "a.txt").write_text("findme")
        (authorized_tmp_dir / "b.txt").write_text("findme")

        result = await grep.ainvoke(
            {
                "pattern": "findme",
                "path": str(authorized_tmp_dir),
                "output_mode": "files_with_matches",
            }
        )
        assert "匹配文件" in result

    async def test_grep_tool_count_format(self, authorized_tmp_dir):
        """count 模式格式化"""
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "test.txt").write_text("foo\nbar\nfoo")

        result = await grep.ainvoke(
            {
                "pattern": "foo",
                "path": str(authorized_tmp_dir),
                "output_mode": "count",
            }
        )
        assert "处匹配" in result

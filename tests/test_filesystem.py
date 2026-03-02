"""文件系统工具测试（helpers + backend + tool wrappers）"""

import pytest

from lumi.agents.tools.providers.filesystem import (
    LocalFilesystemBackend,
    check_empty_content,
    format_content_with_line_numbers,
    perform_string_replacement,
)


# ============================================================================
# Helper 纯函数测试
# ============================================================================


class TestCheckEmptyContent:
    def test_empty_string(self):
        assert check_empty_content("") == "警告: 文件存在但内容为空"

    def test_whitespace_only(self):
        assert check_empty_content("   \n\t ") == "警告: 文件只包含空白字符"

    def test_has_content(self):
        assert check_empty_content("hello") is None


class TestFormatContentWithLineNumbers:
    def test_basic(self):
        result = format_content_with_line_numbers(["aaa", "bbb", "ccc"])
        assert "1\taaa" in result
        assert "2\tbbb" in result
        assert "3\tccc" in result

    def test_offset(self):
        result = format_content_with_line_numbers(["x", "y"], start_line=10)
        assert "10\tx" in result
        assert "11\ty" in result

    def test_empty_list(self):
        assert format_content_with_line_numbers([]) == ""

    def test_alignment(self):
        lines = [f"line{i}" for i in range(11)]
        result = format_content_with_line_numbers(lines, start_line=1)
        # 行号 1-11 的最大宽度为 2，所以 1 应该右对齐
        assert " 1\t" in result
        assert "11\t" in result


class TestPerformStringReplacement:
    def test_normal_replacement(self):
        result = perform_string_replacement("hello world", "world", "earth")
        assert result == ("hello earth", 1)

    def test_replace_all(self):
        result = perform_string_replacement("aXbXc", "X", "Y", replace_all=True)
        assert result == ("aYbYc", 2)

    def test_multiple_matches_no_replace_all(self):
        result = perform_string_replacement("aXbXc", "X", "Y")
        assert isinstance(result, str)
        assert "2" in result  # error message mentions count

    def test_not_found(self):
        result = perform_string_replacement("hello", "xyz", "abc")
        assert isinstance(result, str)
        assert "未找到" in result

    def test_same_string(self):
        result = perform_string_replacement("hello", "hello", "hello")
        assert isinstance(result, str)
        assert "相同" in result

    def test_empty_old_string(self):
        result = perform_string_replacement("hello", "", "x")
        assert isinstance(result, str)
        assert "不能为空" in result


# ============================================================================
# Backend 测试
# ============================================================================


@pytest.fixture
def backend():
    return LocalFilesystemBackend()


class TestBackendRead:
    async def test_read_normal(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "test.txt"
        f.write_text("line1\nline2\nline3")
        result = await backend.read(str(f))
        assert "line1" in result
        assert "line2" in result

    async def test_read_not_exists(self, backend, authorized_tmp_dir):
        result = await backend.read(str(authorized_tmp_dir / "nope.txt"))
        assert "不存在" in result

    async def test_read_offset_limit(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "big.txt"
        f.write_text("\n".join(f"L{i}" for i in range(20)))
        result = await backend.read(str(f), offset=5, limit=3)
        assert "L5" in result
        assert "L7" in result
        assert "L8" not in result

    async def test_read_empty_file(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "empty.txt"
        f.write_text("")
        result = await backend.read(str(f))
        assert "空" in result

    async def test_read_path_outside(self, backend, authorized_tmp_dir):
        result = await backend.read("/etc/passwd")
        assert "错误" in result


class TestBackendWrite:
    async def test_write_new(self, backend, authorized_tmp_dir):
        path = str(authorized_tmp_dir / "new.txt")
        result = await backend.write(path, "content")
        assert result["error"] is None
        assert (authorized_tmp_dir / "new.txt").read_text() == "content"

    async def test_write_auto_mkdir(self, backend, authorized_tmp_dir):
        path = str(authorized_tmp_dir / "a" / "b" / "c.txt")
        result = await backend.write(path, "deep")
        assert result["error"] is None

    async def test_write_existing_rejected(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "exists.txt"
        f.write_text("old")
        result = await backend.write(str(f), "new")
        assert result["error"] is not None
        assert "已存在" in result["error"]


class TestBackendEdit:
    async def test_edit_single(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "e.txt"
        f.write_text("hello world")
        result = await backend.edit(str(f), "world", "earth")
        assert result["error"] is None
        assert result["occurrences"] == 1
        assert f.read_text() == "hello earth"

    async def test_edit_replace_all(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "e2.txt"
        f.write_text("aXbXc")
        result = await backend.edit(str(f), "X", "Y", replace_all=True)
        assert result["error"] is None
        assert result["occurrences"] == 2

    async def test_edit_multiple_no_replace_all(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "e3.txt"
        f.write_text("aXbXc")
        result = await backend.edit(str(f), "X", "Y")
        assert result["error"] is not None

    async def test_edit_not_found(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "e4.txt"
        f.write_text("hello")
        result = await backend.edit(str(f), "xyz", "abc")
        assert result["error"] is not None


class TestBackendLsInfo:
    async def test_ls_with_content(self, backend, authorized_tmp_dir):
        (authorized_tmp_dir / "file.txt").write_text("hi")
        (authorized_tmp_dir / "subdir").mkdir()
        items = await backend.ls_info(str(authorized_tmp_dir))
        assert len(items) == 2
        paths = [i["path"] for i in items]
        assert any("file.txt" in p for p in paths)
        assert any("subdir" in p for p in paths)

    async def test_ls_empty_dir(self, backend, authorized_tmp_dir):
        empty = authorized_tmp_dir / "empty_dir"
        empty.mkdir()
        items = await backend.ls_info(str(empty))
        assert items == []

    async def test_ls_sorted(self, backend, authorized_tmp_dir):
        (authorized_tmp_dir / "b.txt").write_text("b")
        (authorized_tmp_dir / "a.txt").write_text("a")
        items = await backend.ls_info(str(authorized_tmp_dir))
        paths = [i["path"] for i in items]
        assert paths == sorted(paths)


class TestBackendGlobInfo:
    async def test_glob_py(self, backend, authorized_tmp_dir):
        (authorized_tmp_dir / "main.py").write_text("pass")
        (authorized_tmp_dir / "data.txt").write_text("data")
        items = await backend.glob_info("*.py", str(authorized_tmp_dir))
        assert len(items) == 1
        assert "main.py" in items[0]["path"]

    async def test_glob_recursive(self, backend, authorized_tmp_dir):
        sub = authorized_tmp_dir / "pkg"
        sub.mkdir()
        (sub / "mod.py").write_text("pass")
        (authorized_tmp_dir / "top.py").write_text("pass")
        items = await backend.glob_info("**/*.py", str(authorized_tmp_dir))
        assert len(items) == 2

    async def test_glob_no_match(self, backend, authorized_tmp_dir):
        items = await backend.glob_info("*.xyz", str(authorized_tmp_dir))
        assert items == []


class TestBackendGrepRaw:
    async def test_grep_match(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "code.py"
        f.write_text("def hello():\n    pass\ndef world():\n    pass")
        results = await backend.grep_raw("def \\w+", str(authorized_tmp_dir))
        assert isinstance(results, list)
        assert len(results) >= 2

    async def test_grep_invalid_regex(self, backend, authorized_tmp_dir):
        result = await backend.grep_raw("[invalid", str(authorized_tmp_dir))
        assert isinstance(result, str)
        assert "无效" in result

    async def test_grep_no_match(self, backend, authorized_tmp_dir):
        f = authorized_tmp_dir / "empty_search.txt"
        f.write_text("nothing here")
        results = await backend.grep_raw("zzzzz_no_match", str(authorized_tmp_dir))
        assert isinstance(results, list)
        assert len(results) == 0

    async def test_grep_file_glob_filter(self, backend, authorized_tmp_dir):
        (authorized_tmp_dir / "a.py").write_text("target_word")
        (authorized_tmp_dir / "b.txt").write_text("target_word")
        results = await backend.grep_raw(
            "target_word", str(authorized_tmp_dir), file_glob="*.py"
        )
        assert isinstance(results, list)
        assert all("a.py" in r["path"] for r in results)


# ============================================================================
# Tool wrapper 测试
# ============================================================================


class TestToolWrappers:
    """测试 @tool 修饰的函数的返回格式"""

    async def test_read_tool(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import read

        f = authorized_tmp_dir / "r.txt"
        f.write_text("hello\nworld")
        result = await read.ainvoke({"file_path": str(f)})
        assert "hello" in result
        assert "world" in result

    async def test_write_tool_success(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import write

        path = str(authorized_tmp_dir / "w.txt")
        result = await write.ainvoke({"file_path": path, "content": "data"})
        assert "成功" in result

    async def test_write_tool_error(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import write

        f = authorized_tmp_dir / "exists.txt"
        f.write_text("old")
        result = await write.ainvoke({"file_path": str(f), "content": "new"})
        assert "错误" in result

    async def test_edit_tool_success(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import edit

        f = authorized_tmp_dir / "ed.txt"
        f.write_text("foo bar")
        result = await edit.ainvoke(
            {"file_path": str(f), "old_string": "foo", "new_string": "baz"}
        )
        assert "成功" in result
        assert "1" in result

    async def test_ls_tool_format(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import ls

        (authorized_tmp_dir / "file.txt").write_text("hi")
        (authorized_tmp_dir / "dir").mkdir()
        result = await ls.ainvoke({"path": str(authorized_tmp_dir)})
        assert "[目录]" in result
        assert "[文件]" in result

    async def test_grep_tool_format(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import grep

        (authorized_tmp_dir / "g.txt").write_text("findme here")
        result = await grep.ainvoke(
            {"pattern": "findme", "path": str(authorized_tmp_dir)}
        )
        assert "找到" in result and "匹配" in result

"""文件系统工具测试（helpers + backend + tool wrappers）"""

import locale
import os

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
        assert check_empty_content("") == "文件存在但内容为空"

    def test_whitespace_only(self):
        assert check_empty_content("   \n\t ") == "文件只包含空白字符"

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

    async def test_read_non_utf8_falls_back(
        self, backend, authorized_tmp_dir, monkeypatch
    ):
        """非 UTF-8 文件（中文 Windows 上大量 txt/csv 是 GBK）按本地编码兜底并声明。

        提示不占正文行号：正文第一行仍是 1。
        """
        monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale: "gbk")
        f = authorized_tmp_dir / "gbk.txt"
        f.write_bytes("第一行\n第二行".encode("gbk"))
        result = await backend.read(str(f))
        assert "gbk" in result
        assert "\n1\t第一行" in result  # 提示后紧跟正文第 1 行，行号未被顶偏

    @pytest.mark.parametrize("preferred", ["utf-8", "gbk"])
    async def test_read_binary_reports_error(
        self, backend, authorized_tmp_dir, monkeypatch, preferred
    ):
        """二进制文件仍是一句「读不了」，不许变成一屏乱码。

        两条路都得挡住：POSIX 的本地编码就是 UTF-8，同一份字节换 errors="replace"
        再解一遍必然“成功”；GBK 之类的编码更是几乎吃得下任意字节。
        """
        monkeypatch.setattr(
            locale, "getpreferredencoding", lambda _do_setlocale: preferred
        )
        f = authorized_tmp_dir / "x.bin"
        f.write_bytes(bytes(range(256)) * 20)
        assert "错误" in await backend.read(str(f))

    async def test_read_path_outside(self, backend, authorized_tmp_dir):
        # 使用当前系统上一定存在但在授权目录之外的路径
        import tempfile

        outside_path = os.path.join(tempfile.gettempdir(), "lumi_test_outside.txt")
        result = await backend.read(outside_path)
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

    async def test_edit_keeps_crlf(self, backend, authorized_tmp_dir):
        """CRLF 文件改一行后仍是 CRLF：否则一次小改动会变成全文件 diff。

        old_string 用 \\n 也要能命中——匹配前统一成 LF，写回时再还原。
        """
        f = authorized_tmp_dir / "crlf.txt"
        f.write_bytes(b"a\r\nb\r\nc\r\n")
        result = await backend.edit(str(f), "a\nb", "a\nB")
        assert result["error"] is None
        assert f.read_bytes() == b"a\r\nB\r\nc\r\n"

    async def test_edit_keeps_mixed_line_endings(self, backend, authorized_tmp_dir):
        """混合行尾的文件只改命中那段，没被改到的行原样不动。

        （CSV 引号内 CRLF、Windows/Unix 工具交替动过的文件都是这形状）
        """
        f = authorized_tmp_dir / "mixed.csv"
        f.write_bytes(b"x\r\ny\nz\n")
        result = await backend.edit(str(f), "x", "X")
        assert result["error"] is None
        assert f.read_bytes() == b"X\r\ny\nz\n"

    async def test_edit_keeps_lf(self, backend, authorized_tmp_dir):
        """LF 文件不许被写成 CRLF（Windows 上 write_text 默认会译成 os.linesep）。"""
        f = authorized_tmp_dir / "lf.txt"
        f.write_bytes(b"a\nb\nc\n")
        result = await backend.edit(str(f), "b", "B")
        assert result["error"] is None
        assert f.read_bytes() == b"a\nB\nc\n"

    async def test_write_keeps_lf(self, backend, authorized_tmp_dir):
        """新建文件按模型给的原文落盘，三平台字节一致。"""
        f = authorized_tmp_dir / "new_lf.txt"
        await backend.write(str(f), "a\nb\n")
        assert f.read_bytes() == b"a\nb\n"


# ============================================================================
# Tool wrapper 测试
# ============================================================================


class TestToolWrappers:
    """测试 @tool 修饰的函数的返回格式"""

    async def test_read_tool(self, authorized_tmp_dir):
        from lumi.agents.tools.providers.filesystem import read

        f = authorized_tmp_dir / "r.txt"
        f.write_text("hello\nworld")
        # 使用完整 ToolCall 格式调用,以满足 InjectedToolCallId 的注入要求
        tool_call = {
            "name": "read",
            "args": {"file_path": str(f)},
            "id": "test_call_1",
            "type": "tool_call",
        }
        result = await read.ainvoke(tool_call)
        assert hasattr(result, "content")
        assert "hello" in result.content
        assert "world" in result.content

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


async def test_multiline_edit_in_mixed_line_ending_file(authorized_tmp_dir):
    # 回归：文件里出现过 CRLF 就把 old_string 整体换成 CRLF，混合行尾文件里跨 LF 行的
    # 多行编辑必然「未找到」
    from lumi.agents.tools.providers.filesystem.tools import edit

    f = authorized_tmp_dir / "mixed.txt"
    f.write_bytes(b"header\r\nb\nc\n")
    out = await edit.ainvoke(
        {"file_path": str(f), "old_string": "b\nc", "new_string": "B\nC"}
    )
    assert out.startswith("成功"), out
    assert f.read_bytes() == b"header\r\nB\nC\n"


async def test_edit_error_has_single_prefix(authorized_tmp_dir):
    from lumi.agents.tools.providers.filesystem.tools import edit

    f = authorized_tmp_dir / "a.txt"
    f.write_text("hello\n")
    out = await edit.ainvoke(
        {"file_path": str(f), "old_string": "zzz", "new_string": "y"}
    )
    assert out.count("错误") == 1, out


async def test_read_line_numbers_match_rg_with_form_feed(authorized_tmp_dir):
    # 回归：splitlines() 在 \f、U+2028 等处额外断行，read 的行号与 grep 不一致，
    # 按 read 视图写的 old_string 也对不上
    f = authorized_tmp_dir / "ff.txt"
    f.write_text("line1\fstill1\nline2 still2\nline3\n")
    out = await LocalFilesystemBackend().read(str(f))
    rows = out.split("\n")
    assert len(rows) == 3
    assert rows[2].endswith("\tline3")


async def test_read_runs_off_event_loop(authorized_tmp_dir, monkeypatch):
    # 回归：read 在事件循环线程上同步读盘 + 解码（150MB 文件卡住整个网关 1.7s，所有
    # 会话、飞书、cron 共用这一个循环）
    import threading
    from pathlib import Path

    f = authorized_tmp_dir / "a.txt"
    f.write_text("hello\n")
    on_loop_thread: list[bool] = []
    original = Path.read_bytes

    def spy(self):
        on_loop_thread.append(threading.current_thread() is threading.main_thread())
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    await LocalFilesystemBackend().read(str(f))
    assert on_loop_thread == [False]

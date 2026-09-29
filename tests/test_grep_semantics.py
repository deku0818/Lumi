"""grep 的正则方言、报错、计数与分页语义（回归，走真实 rg）。"""

from __future__ import annotations

from lumi.agents.tools.providers.filesystem import LocalFilesystemBackend
from lumi.agents.tools.providers.filesystem.tools import grep


async def _grep(**kw) -> str:
    return await grep.ainvoke(kw)


async def test_rg_only_regex_syntax_works(authorized_tmp_dir):
    # Python re 预校验曾误拒 rg 支持的语法（\p{Han}、(?<name>…)）
    (authorized_tmp_dir / "a.txt").write_text("hello 中文\n")
    out = await _grep(
        pattern=r"\p{Han}+", path=str(authorized_tmp_dir), output_mode="content"
    )
    assert "中文" in out


async def test_rg_errors_are_reported(authorized_tmp_dir):
    # rg 报错（不支持的语法 / 未知 type / 路径不存在）曾被吞成「未找到匹配」
    (authorized_tmp_dir / "a.txt").write_text("hello\n")
    for kw in (
        {"pattern": r"(l)\1"},
        {"pattern": "hello", "type": "pyy"},
        {"pattern": "hello", "path": str(authorized_tmp_dir / "nope")},
    ):
        out = await _grep(
            **{"path": str(authorized_tmp_dir), "output_mode": "content", **kw}
        )
        assert "错误" in out, (kw, out)


async def test_multiline_dot_matches_newline(authorized_tmp_dir):
    (authorized_tmp_dir / "a.txt").write_text("foo\nbar\n")
    out = await _grep(
        pattern="foo.bar",
        path=str(authorized_tmp_dir),
        multiline=True,
        output_mode="count",
    )
    assert "1 处匹配" in out


async def test_count_on_single_file(authorized_tmp_dir):
    # 单文件 count 曾恒为空（rg 不带文件名，解析时被丢弃）
    f = authorized_tmp_dir / "a.txt"
    f.write_text("x\nx\n")
    out = await _grep(pattern="x", path=str(f), output_mode="count")
    assert "2 处匹配" in out


async def test_context_lines_not_counted_as_matches(authorized_tmp_dir):
    (authorized_tmp_dir / "a.txt").write_text("a\nb\nHIT\nc\nd\n")
    backend = LocalFilesystemBackend()
    result = await backend.grep_raw("HIT", str(authorized_tmp_dir), context=2)
    assert result["total"] == 1


async def test_files_mode_reports_total_when_truncated(authorized_tmp_dir):
    for i in range(30):
        (authorized_tmp_dir / f"f{i:02}.txt").write_text("hit\n")
    out = await _grep(pattern="hit", path=str(authorized_tmp_dir), head_limit=3)
    assert "30" in out and "已截断" in out


async def test_pagination_is_stable(authorized_tmp_dir):
    # rg 多线程输出顺序不定：按页翻完曾漏掉大量文件
    for i in range(120):
        (authorized_tmp_dir / f"f{i:03}.txt").write_text("hit\n")
    backend = LocalFilesystemBackend()
    seen: set[str] = set()
    for offset in range(0, 120, 20):
        page = await backend.grep_raw(
            "hit",
            str(authorized_tmp_dir),
            output_mode="files_with_matches",
            offset=offset,
            head_limit=20,
        )
        seen.update(item["path"] for item in page["items"])
    assert len(seen) == 120


async def test_overlong_line_is_truncated(authorized_tmp_dir):
    (authorized_tmp_dir / "min.js").write_text("hit" + "x" * 100_000 + "\n")
    out = await _grep(
        pattern="hit", path=str(authorized_tmp_dir), output_mode="content"
    )
    assert len(out) < 2000


async def test_missing_rg_asks_to_install(authorized_tmp_dir, monkeypatch):
    # 没有 rg 时不再降级到纯 Python 扫描（不认 .gitignore、不支持 type / 上下文 /
    # 多行，结果与 rg 语义不一致），直接给出安装办法
    (authorized_tmp_dir / "a.txt").write_text("hello\n")

    async def no_rg(self, cmd):
        return None

    monkeypatch.setattr(LocalFilesystemBackend, "_run_ripgrep", no_rg)
    out = await _grep(pattern="hello", path=str(authorized_tmp_dir))
    assert "lumi env install rg" in out

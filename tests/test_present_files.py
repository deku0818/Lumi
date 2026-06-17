"""present_files 工具测试 — 纯元数据收集，不做任何上传/打开等副作用。"""

import json

from lumi.agents.tools.providers.present_files import _categorize, present_files


def test_categorize_by_extension():
    assert _categorize("a.png", "image/png") == "image"
    assert _categorize("a.pdf", "application/pdf") == "pdf"
    assert _categorize("a.mp4", "video/mp4") == "video"
    assert _categorize("a.docx", "application/octet-stream") == "doc"
    assert _categorize("a.xlsx", "application/octet-stream") == "sheet"


def test_categorize_mime_fallback():
    # 扩展名未知时按 mime 兜底（.md 在部分系统 guess 不出 mime）
    assert _categorize("a.unknownext", "text/plain") == "text"
    assert _categorize("a.bin", "application/octet-stream") == "file"


def test_present_existing_file(tmp_path):
    f = tmp_path / "report.md"
    f.write_text("hello")
    out = json.loads(present_files.invoke({"filepaths": [str(f)]}))
    assert len(out) == 1
    item = out[0]
    assert item["path"] == str(f)
    assert item["name"] == "report.md"
    assert item["size"] == 5
    assert "error" not in item


def test_present_missing_file_reports_error(tmp_path):
    out = json.loads(present_files.invoke({"filepaths": [str(tmp_path / "nope.png")]}))
    assert out[0]["error"] == "文件不存在"
    assert "size" not in out[0]


def test_order_preserved(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    out = json.loads(present_files.invoke({"filepaths": [str(b), str(a)]}))
    assert [i["name"] for i in out] == ["b.txt", "a.txt"]

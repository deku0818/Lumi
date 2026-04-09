"""utils/jsonc.py 单元测试。"""

from __future__ import annotations

import json

import pytest

from lumi.utils.jsonc import parse_jsonc, strip_jsonc_comments


class TestStripJsoncComments:
    def test_no_comments(self):
        text = '{"key": "value"}'
        assert strip_jsonc_comments(text) == text

    def test_single_line_comment(self):
        text = '{\n  // this is a comment\n  "key": "value"\n}'
        result = strip_jsonc_comments(text)
        assert "//" not in result
        assert '"key": "value"' in result

    def test_block_comment(self):
        text = '{\n  /* block comment */\n  "key": "value"\n}'
        result = strip_jsonc_comments(text)
        assert "/*" not in result
        assert '"key": "value"' in result

    def test_multiline_block_comment(self):
        text = '{\n  /* line1\n     line2\n     line3 */\n  "key": 1\n}'
        result = strip_jsonc_comments(text)
        assert "/*" not in result
        assert "line1" not in result
        assert '"key": 1' in result

    def test_comment_inside_string_preserved(self):
        text = '{"url": "http://example.com", "pattern": "// not a comment"}'
        result = strip_jsonc_comments(text)
        assert "// not a comment" in result

    def test_block_comment_inside_string_preserved(self):
        text = '{"code": "/* not a comment */"}'
        result = strip_jsonc_comments(text)
        assert "/* not a comment */" in result

    def test_escaped_quotes_in_string(self):
        text = r'{"msg": "he said \"hello\" // world", "a": 1}'
        result = strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed["a"] == 1
        assert "// world" in parsed["msg"]

    def test_unclosed_block_comment(self):
        text = '{"key": 1}\n/* unclosed comment'
        result = strip_jsonc_comments(text)
        assert "unclosed" not in result
        assert '"key": 1' in result

    def test_empty_input(self):
        assert strip_jsonc_comments("") == ""

    def test_inline_comment_after_value(self):
        text = '{\n  "a": 1, // inline\n  "b": 2\n}'
        result = strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}


class TestParseJsonc:
    def test_basic(self):
        text = '{\n  // comment\n  "key": "value"\n}'
        assert parse_jsonc(text) == {"key": "value"}

    def test_complex_config(self):
        text = """{
  // 用户配置
  "allow": [
    {"tool": "bash", "command": "ls *"}, /* 允许 ls */
    {"tool": "read"}
  ],
  "deny": []
}"""
        result = parse_jsonc(text)
        assert len(result["allow"]) == 2
        assert result["allow"][0]["tool"] == "bash"
        assert result["deny"] == []

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_jsonc("{invalid json}")

    def test_trailing_comma_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_jsonc('{"a": 1,}')

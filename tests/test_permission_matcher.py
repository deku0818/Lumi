"""RuleMatcher 单元测试"""

from pathlib import Path

from lumi.agents.tools.capability import split_compound_command
from lumi.agents.tools.permissions.matcher import (
    RuleMatcher,
    build_exact_expr,
    build_pattern_expr,
)
from lumi.agents.tools.permissions.models import Permission, PermissionRule


class TestParseToolExpression:
    """parse_tool_expression 测试"""

    def test_plain_tool_name(self):
        assert RuleMatcher.parse_tool_expression("read") == ("read", None)

    def test_plain_tool_name_with_spaces(self):
        assert RuleMatcher.parse_tool_expression("  read  ") == ("read", None)

    def test_bash_with_command_pattern(self):
        assert RuleMatcher.parse_tool_expression("bash(npm *)") == ("bash", "npm *")

    def test_edit_with_path_pattern(self):
        assert RuleMatcher.parse_tool_expression("edit(src/**/*.py)") == (
            "edit",
            "src/**/*.py",
        )

    def test_write_with_glob(self):
        assert RuleMatcher.parse_tool_expression("write(*.log)") == ("write", "*.log")

    def test_mcp_tool_name(self):
        assert RuleMatcher.parse_tool_expression("mcp_server_tool") == (
            "mcp_server_tool",
            None,
        )

    def test_bash_with_exact_command(self):
        assert RuleMatcher.parse_tool_expression("bash(npm test)") == (
            "bash",
            "npm test",
        )


class TestMatchCommandPattern:
    """match_command_pattern 测试"""

    def test_exact_match(self):
        assert RuleMatcher.match_command_pattern("npm test", "npm test") is True

    def test_exact_no_match(self):
        assert RuleMatcher.match_command_pattern("npm test", "npm run build") is False

    def test_wildcard_suffix(self):
        assert RuleMatcher.match_command_pattern("npm *", "npm test") is True
        assert RuleMatcher.match_command_pattern("npm *", "npm run build") is True

    def test_wildcard_no_match(self):
        assert RuleMatcher.match_command_pattern("npm *", "yarn test") is False

    def test_wildcard_prefix(self):
        assert RuleMatcher.match_command_pattern("* test", "npm test") is True
        assert RuleMatcher.match_command_pattern("* test", "yarn test") is True

    def test_multiple_wildcards(self):
        assert RuleMatcher.match_command_pattern("* * build", "npm run build") is True

    def test_empty_wildcard_match(self):
        # * 匹配空字符串
        assert RuleMatcher.match_command_pattern("npm*", "npm") is True

    def test_special_regex_chars_escaped(self):
        # 确保正则特殊字符被正确转义
        assert RuleMatcher.match_command_pattern("rm -rf .", "rm -rf .") is True
        assert RuleMatcher.match_command_pattern("echo (hello)", "echo (hello)") is True

    def test_invalid_pattern_returns_false(self):
        # 无效模式应返回 False（不会抛异常）
        # 由于我们先 re.escape 再替换，实际上很难构造出无效正则
        # 但确保不会崩溃
        assert RuleMatcher.match_command_pattern("", "") is True


class TestMatchPathPattern:
    """match_path_pattern 测试"""

    def test_single_star_matches_filename(self):
        project = Path("/project")
        assert RuleMatcher.match_path_pattern("*.py", "src/main.py", project) is True
        assert RuleMatcher.match_path_pattern("*.py", "main.py", project) is True

    def test_single_star_no_slash(self):
        project = Path("/project")
        # * 不匹配 /
        assert (
            RuleMatcher.match_path_pattern("*.py", "src/sub/main.py", project) is True
        )  # *.py 在任意层级匹配

    def test_double_star_matches_multi_level(self):
        project = Path("/project")
        assert (
            RuleMatcher.match_path_pattern("src/**/*.py", "src/a/b/c.py", project)
            is True
        )
        assert (
            RuleMatcher.match_path_pattern("src/**/*.py", "src/main.py", project)
            is True
        )

    def test_anchored_pattern(self):
        project = Path("/project")
        # 带 / 前缀从根匹配
        assert (
            RuleMatcher.match_path_pattern("/src/*.py", "src/main.py", project) is True
        )
        assert (
            RuleMatcher.match_path_pattern("/src/*.py", "lib/src/main.py", project)
            is False
        )

    def test_unanchored_pattern_matches_any_level(self):
        project = Path("/project")
        # 不带 / 前缀在任意层级匹配
        assert RuleMatcher.match_path_pattern("*.log", "logs/app.log", project) is True
        assert RuleMatcher.match_path_pattern("*.log", "a/b/c.log", project) is True

    def test_absolute_path_relative_to_project(self):
        project = Path("/project")
        assert (
            RuleMatcher.match_path_pattern("src/*.py", "/project/src/main.py", project)
            is True
        )

    def test_absolute_path_outside_project(self):
        project = Path("/project")
        assert (
            RuleMatcher.match_path_pattern("*.py", "/other/main.py", project) is False
        )


class TestMatchRule:
    """match_rule 测试"""

    def test_plain_tool_name_match(self):
        rule = PermissionRule(tool="read", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "read", {}) is True

    def test_plain_tool_name_no_match(self):
        rule = PermissionRule(tool="read", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "write", {}) is False

    def test_bash_command_match(self):
        rule = PermissionRule(tool="bash(npm *)", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "bash", {"command": "npm test"}) is True

    def test_bash_command_no_match(self):
        rule = PermissionRule(tool="bash(npm *)", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "bash", {"command": "yarn test"}) is False

    def test_bash_no_command_arg(self):
        rule = PermissionRule(tool="bash(npm *)", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "bash", {"other": "value"}) is False

    def test_path_tool_match(self):
        rule = PermissionRule(tool="edit(src/**/*.py)", permission=Permission.ALLOW)
        assert RuleMatcher.match_rule(rule, "edit", {"path": "src/main.py"}) is True

    def test_path_tool_no_match(self):
        rule = PermissionRule(tool="write(*.log)", permission=Permission.DENY)
        assert RuleMatcher.match_rule(rule, "write", {"path": "src/main.py"}) is False

    def test_deny_rule_matches(self):
        rule = PermissionRule(tool="bash(rm -rf *)", permission=Permission.DENY)
        assert RuleMatcher.match_rule(rule, "bash", {"command": "rm -rf /"}) is True


class TestSplitCompoundCommand:
    """split_compound_command 测试（纯字符串操作，不执行任何命令）"""

    def test_single_command(self):
        assert split_compound_command("git push") == ["git push"]

    def test_and_operator(self):
        result = split_compound_command("git add . && git commit -m msg")
        assert result == ["git add .", "git commit -m msg"]

    def test_or_operator(self):
        result = split_compound_command("test -f foo || echo missing")
        assert result == ["test -f foo", "echo missing"]

    def test_semicolon(self):
        result = split_compound_command("echo a ; echo b")
        assert result == ["echo a", "echo b"]

    def test_pipe(self):
        result = split_compound_command("ls | grep foo")
        assert result == ["ls", "grep foo"]

    def test_multiple_separators(self):
        result = split_compound_command("a && b ; c | d")
        assert len(result) == 4
        assert result[0] == "a"
        assert result[1] == "b"
        assert result[2] == "c"
        assert result[3] == "d"

    def test_quoted_separator_not_split(self):
        # 引号内的 && 不应拆分
        result = split_compound_command("echo 'a && b'")
        assert len(result) == 1
        assert "a && b" in result[0]

    def test_empty_command(self):
        assert split_compound_command("") == []

    def test_whitespace_only(self):
        result = split_compound_command("   ")
        assert len(result) <= 1

    def test_unclosed_quote_returns_original(self):
        # 引号未闭合时不拆分，返回原命令
        result = split_compound_command("echo 'hello && world")
        assert len(result) == 1

    def test_background_operator(self):
        result = split_compound_command("sleep 1 & echo done")
        assert result == ["sleep 1", "echo done"]

    def test_double_quoted_separator_not_split(self):
        """双引号内的 && 不应拆分"""
        result = split_compound_command('echo "a && b"')
        assert len(result) == 1
        assert "a && b" in result[0]

    def test_bash_c_quoted_not_split(self):
        """bash -c 内的引号命令不应拆分"""
        result = split_compound_command('bash -c "git push && echo done"')
        assert len(result) == 1
        assert "git push && echo done" in result[0]

    def test_unclosed_quote_not_split(self):
        """引号未闭合时不拆分（引号内的分隔符被忽略）"""
        result = split_compound_command("echo 'hello && world")
        assert len(result) == 1


class TestBuildExactExpr:
    """build_exact_expr 测试"""

    def test_bash_tool(self):
        assert build_exact_expr("bash", {"command": "npm test"}) == "bash(npm test)"

    def test_path_tool(self):
        assert (
            build_exact_expr("edit", {"file_path": "src/main.py"})
            == "edit(src/main.py)"
        )

    def test_no_args(self):
        assert build_exact_expr("bash", {}) == "bash"

    def test_other_tool(self):
        assert build_exact_expr("agent", {"prompt": "hi"}) == "agent"


class TestBuildPatternExpr:
    """build_pattern_expr 测试"""

    def test_bash_tool(self):
        assert build_pattern_expr("bash", {"command": "npm test"}) == "bash(npm *)"

    def test_path_tool_with_extension(self):
        assert (
            build_pattern_expr("edit", {"file_path": "src/main.py"}) == "edit(**/*.py)"
        )

    def test_path_tool_without_extension(self):
        assert build_pattern_expr("edit", {"file_path": "Makefile"}) == "edit(**/*)"

    def test_no_args(self):
        assert build_pattern_expr("bash", {}) == "bash"

    def test_other_tool(self):
        assert build_pattern_expr("agent", {"prompt": "hi"}) == "agent"

    def test_pattern_differs_from_exact(self):
        exact = build_exact_expr("bash", {"command": "npm test"})
        pattern = build_pattern_expr("bash", {"command": "npm test"})
        assert exact != pattern

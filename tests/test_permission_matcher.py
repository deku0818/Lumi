"""RuleMatcher 单元测试"""

from pathlib import Path

from lumi.agents.tools.permissions.matcher import RuleMatcher
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

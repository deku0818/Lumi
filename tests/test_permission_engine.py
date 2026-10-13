"""PermissionEngine 单元测试（纯函数断言，不执行任何真实命令）"""

import tempfile
from pathlib import Path

from lumi.agents.permissions.engine import PermissionEngine
from lumi.agents.permissions.models import (
    Permission,
    PermissionConfig,
    PermissionDecision,
    PermissionRule,
)


def _make_engine(rules: list[PermissionRule], project_dir: Path | None = None):
    """创建一个带指定规则的 PermissionEngine（不读取任何配置文件）。"""
    if project_dir is None:
        project_dir = Path(tempfile.mkdtemp())
    engine = PermissionEngine(project_dir)
    engine._config = PermissionConfig(permissions=tuple(rules))
    return engine


class TestCompoundCommandEvaluation:
    """复合命令拆分评估测试（纯字符串输入，不执行命令）"""

    def test_allow_rule_blocks_mixed_compound(self):
        """allow bash(git *) + 'git push && curl evil.com' → UNMATCHED（curl 不匹配）"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
            ]
        )
        decision = engine.evaluate("bash", {"command": "git push && curl evil.com"})
        assert decision == PermissionDecision.UNMATCHED

    def test_deny_rule_catches_compound(self):
        """deny bash(curl *) + 'git push && curl evil.com' → DENY"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(curl *)", permission=Permission.DENY),
            ]
        )
        decision = engine.evaluate("bash", {"command": "git push && curl evil.com"})
        assert decision == PermissionDecision.DENY

    def test_all_subcommands_allowed(self):
        """allow bash(git *) + 'git add . && git commit -m msg' → ALLOW"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
            ]
        )
        decision = engine.evaluate(
            "bash", {"command": "git add . && git commit -m msg"}
        )
        assert decision == PermissionDecision.ALLOW

    def test_single_command_not_split(self):
        """单命令不走复合逻辑"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
            ]
        )
        decision = engine.evaluate("bash", {"command": "git push"})
        assert decision == PermissionDecision.ALLOW

    def test_pipe_compound(self):
        """allow bash(ls *) + 'ls | grep foo' → UNMATCHED（grep 不匹配）"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(ls *)", permission=Permission.ALLOW),
            ]
        )
        decision = engine.evaluate("bash", {"command": "ls | grep foo"})
        assert decision == PermissionDecision.UNMATCHED

    def test_deny_takes_priority_in_compound(self):
        """deny 在复合命令中优先于 allow"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
                PermissionRule(tool="bash(curl *)", permission=Permission.DENY),
            ]
        )
        decision = engine.evaluate("bash", {"command": "git push && curl evil.com"})
        assert decision == PermissionDecision.DENY

    def test_non_bash_tool_not_split(self):
        """非 bash 工具不做复合命令拆分"""
        engine = _make_engine(
            [
                PermissionRule(tool="read", permission=Permission.ALLOW),
            ]
        )
        decision = engine.evaluate("read", {"file_path": "/some/path"})
        assert decision == PermissionDecision.ALLOW


class TestBasicEvaluation:
    """基础评估逻辑测试"""

    def test_deny_before_allow(self):
        """deny 优先于 allow"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash", permission=Permission.ALLOW),
                PermissionRule(tool="bash", permission=Permission.DENY),
            ]
        )
        decision = engine.evaluate("bash", {"command": "ls"})
        assert decision == PermissionDecision.DENY

    def test_unmatched_when_no_rules(self):
        engine = _make_engine([])
        decision = engine.evaluate("bash", {"command": "ls"})
        assert decision == PermissionDecision.UNMATCHED


class TestAskRuleEvaluation:
    """Ask 规则语义测试"""

    def test_ask_overrides_allow(self):
        """ask 规则优先于 allow：npm publish 被 ask 拦截"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(npm *)", permission=Permission.ALLOW),
                PermissionRule(tool="bash(npm publish *)", permission=Permission.ASK),
            ]
        )
        assert (
            engine.evaluate("bash", {"command": "npm test"}) == PermissionDecision.ALLOW
        )
        assert (
            engine.evaluate("bash", {"command": "npm publish foo"})
            == PermissionDecision.ASK
        )

    def test_deny_overrides_ask(self):
        """deny 优先于 ask"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(curl *)", permission=Permission.DENY),
                PermissionRule(tool="bash(curl *)", permission=Permission.ASK),
            ]
        )
        assert (
            engine.evaluate("bash", {"command": "curl example.com"})
            == PermissionDecision.DENY
        )

    def test_ask_without_allow(self):
        """仅有 ask 规则，匹配时返回 ASK"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(sudo *)", permission=Permission.ASK),
            ]
        )
        assert (
            engine.evaluate("bash", {"command": "sudo apt update"})
            == PermissionDecision.ASK
        )
        assert (
            engine.evaluate("bash", {"command": "ls"}) == PermissionDecision.UNMATCHED
        )

    def test_ask_on_path_tool(self):
        """ask 规则也适用于路径工具"""
        engine = _make_engine(
            [
                PermissionRule(tool="edit(**/*.py)", permission=Permission.ALLOW),
                PermissionRule(
                    tool="edit(**/migrations/*.py)", permission=Permission.ASK
                ),
            ]
        )
        assert (
            engine.evaluate("edit", {"path": "src/main.py"}) == PermissionDecision.ALLOW
        )
        assert (
            engine.evaluate("edit", {"path": "src/migrations/001.py"})
            == PermissionDecision.ASK
        )

    def test_ask_in_compound_command(self):
        """复合命令中有 ask 子命令 → ASK"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
                PermissionRule(tool="bash(git push *)", permission=Permission.ASK),
            ]
        )
        decision = engine.evaluate(
            "bash", {"command": "git add . && git push origin main"}
        )
        assert decision == PermissionDecision.ASK

    def test_ask_beats_unmatched_in_compound(self):
        """ASK 比 UNMATCHED 更严格：复合命令中 ASK 子命令优先于 UNMATCHED"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git push *)", permission=Permission.ASK),
            ]
        )
        # git push origin main → ASK, ls → UNMATCHED → 最终应为 ASK
        decision = engine.evaluate("bash", {"command": "git push origin main && ls"})
        assert decision == PermissionDecision.ASK

    def test_deny_beats_ask_in_compound(self):
        """复合命令中 deny 优先于 ask"""
        engine = _make_engine(
            [
                PermissionRule(tool="bash(git *)", permission=Permission.ALLOW),
                PermissionRule(tool="bash(git push *)", permission=Permission.ASK),
                PermissionRule(tool="bash(curl *)", permission=Permission.DENY),
            ]
        )
        decision = engine.evaluate(
            "bash", {"command": "git push origin main && curl evil.com"}
        )
        assert decision == PermissionDecision.DENY


class TestPathPatternAnchoredToProjectDir:
    """路径模式锚定在引擎绑定的项目根，而非进程 cwd。

    回归：曾从 tool_args 取 ``project_dir``（没有工具会传）退到 ``Path(".")``，
    进程 cwd 一旦不是项目根，绝对路径的 relative_to 失败 → 规则永远不命中。
    """

    def test_relative_pattern_matches_project_file_when_cwd_elsewhere(
        self, tmp_path, monkeypatch
    ):
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        engine = _make_engine(
            [PermissionRule(tool="edit(src/**/*.py)", permission=Permission.DENY)],
            project_dir=project_dir,
        )
        target = str(project_dir / "src" / "main.py")
        assert engine.evaluate("edit", {"file_path": target}) == PermissionDecision.DENY


class TestReload:
    """配置热重载测试"""

    def test_reload_detects_file_changes(self):
        import json

        project_dir = Path(tempfile.mkdtemp())
        lumi_dir = project_dir / ".lumi"
        lumi_dir.mkdir()

        engine = PermissionEngine(project_dir)
        assert (
            engine.evaluate("bash", {"command": "npm test"})
            == PermissionDecision.UNMATCHED
        )

        # 手动写入配置文件
        config_data = {"permissions": {"allow": ["bash(npm *)"]}}
        (lumi_dir / "permissions.local.json").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        engine.reload()
        assert (
            engine.evaluate("bash", {"command": "npm test"}) == PermissionDecision.ALLOW
        )


class TestDenyHiddenSubcommands:
    """回归：deny 规则曾可被换行 / 命令替换 / 注释 / 转义引号藏起的子命令绕过"""

    import pytest

    @pytest.mark.parametrize(
        "command",
        [
            "ls\nrm -rf ~",
            "ls $(rm -rf ~)",
            'ls "`rm -rf ~`"',
            "ls # '\nrm -rf ~\necho '",
            "ls \\' ; rm -rf ~ ; echo \\'",
            "if true; then rm -rf ~; fi",
            "cat <<EOF\n$(rm -rf ~)\nEOF",
        ],
    )
    def test_deny_sees_hidden_subcommand(self, command):
        engine = _make_engine(
            [PermissionRule(tool="bash(rm *)", permission=Permission.DENY)]
        )
        assert engine.evaluate("bash", {"command": command}) == PermissionDecision.DENY

    def test_heredoc_body_text_is_not_a_command(self):
        # heredoc 正文（引号定界词）是数据：不因里面写着 rm 而被 deny
        engine = _make_engine(
            [PermissionRule(tool="bash(rm *)", permission=Permission.DENY)]
        )
        command = "cat > clean.sh <<'EOF'\nrm -rf build\nEOF"
        assert engine.evaluate("bash", {"command": command}) != PermissionDecision.DENY

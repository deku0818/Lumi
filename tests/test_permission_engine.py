"""PermissionEngine 单元测试（纯函数断言，不执行任何真实命令）"""

import tempfile
from pathlib import Path

from lumi.agents.tools.permissions.engine import PermissionEngine
from lumi.agents.tools.permissions.models import (
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

    def test_invalid_tool_name(self):
        engine = _make_engine([])
        assert engine.evaluate("", {}) == PermissionDecision.UNMATCHED
        assert engine.evaluate(None, {}) == PermissionDecision.UNMATCHED  # type: ignore

    def test_invalid_tool_args(self):
        engine = _make_engine([])
        assert engine.evaluate("bash", "not a dict") == PermissionDecision.UNMATCHED  # type: ignore


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


class TestEphemeralRules:
    """CLI --allow 临时规则测试"""

    def test_ephemeral_rules_take_effect(self):
        engine = _make_engine([])
        assert (
            engine.evaluate("bash", {"command": "npm test"})
            == PermissionDecision.UNMATCHED
        )

        engine.add_ephemeral_rules(["bash(npm *)"])
        assert (
            engine.evaluate("bash", {"command": "npm test"}) == PermissionDecision.ALLOW
        )

    def test_ephemeral_rules_deduplicate(self):
        engine = _make_engine(
            [
                PermissionRule(tool="bash(npm *)", permission=Permission.ALLOW),
            ]
        )
        original_count = len(engine._config.permissions)
        engine.add_ephemeral_rules(["bash(npm *)"])
        assert len(engine._config.permissions) == original_count

    def test_ephemeral_rules_do_not_override_deny(self):
        engine = _make_engine(
            [
                PermissionRule(tool="bash(curl *)", permission=Permission.DENY),
            ]
        )
        engine.add_ephemeral_rules(["bash(curl *)"])
        # deny 仍然优先
        assert (
            engine.evaluate("bash", {"command": "curl evil.com"})
            == PermissionDecision.DENY
        )

    def test_empty_ephemeral_rules(self):
        engine = _make_engine([])
        engine.add_ephemeral_rules([])
        assert (
            engine.evaluate("bash", {"command": "ls"}) == PermissionDecision.UNMATCHED
        )


class TestWorkspaceBoundary:
    """工作区边界检查测试"""

    def test_path_inside_project_is_within_boundary(self):
        project_dir = Path(tempfile.mkdtemp())
        engine = PermissionEngine(project_dir)
        assert (
            engine.check_workspace_boundary(
                "write", {"file_path": str(project_dir / "src" / "main.py")}
            )
            is True
        )

    def test_path_outside_project_is_out_of_boundary(self):
        project_dir = Path(tempfile.mkdtemp())
        engine = PermissionEngine(project_dir)
        assert (
            engine.check_workspace_boundary("write", {"file_path": "/etc/passwd"})
            is False
        )

    def test_no_path_arg_is_within_boundary(self):
        """无法提取路径时视为边界内（保守放行）"""
        project_dir = Path(tempfile.mkdtemp())
        engine = PermissionEngine(project_dir)
        assert engine.check_workspace_boundary("bash", {"command": "ls"}) is True

    def test_get_boundary_violations(self):
        project_dir = Path(tempfile.mkdtemp())
        engine = PermissionEngine(project_dir)
        violations = engine.get_boundary_violations(
            "write", {"file_path": "/etc/passwd"}
        )
        assert len(violations) > 0

    def test_add_workspace_expands_boundary(self):
        project_dir = Path(tempfile.mkdtemp())
        extra_dir = Path(tempfile.mkdtemp())
        engine = PermissionEngine(project_dir)
        # 额外目录初始在边界外
        assert (
            engine.check_workspace_boundary(
                "write", {"file_path": str(extra_dir / "file.txt")}
            )
            is False
        )
        # 添加后应在边界内
        engine.add_workspace(str(extra_dir))
        assert (
            engine.check_workspace_boundary(
                "write", {"file_path": str(extra_dir / "file.txt")}
            )
            is True
        )


class TestPersistence:
    """规则持久化测试"""

    def test_add_allow_rule_persists_to_local_config(self):
        project_dir = Path(tempfile.mkdtemp())
        (project_dir / ".lumi").mkdir()
        engine = PermissionEngine(project_dir)
        engine.add_allow_rule("bash(npm *)")

        # 重新加载验证规则已持久化
        engine2 = PermissionEngine(project_dir)
        assert (
            engine2.evaluate("bash", {"command": "npm test"})
            == PermissionDecision.ALLOW
        )

    def test_add_allow_rule_deduplicates(self):
        project_dir = Path(tempfile.mkdtemp())
        (project_dir / ".lumi").mkdir()
        engine = PermissionEngine(project_dir)
        engine.add_allow_rule("bash(npm *)")
        engine.add_allow_rule("bash(npm *)")

        allow_count = sum(
            1
            for r in engine.config.permissions
            if r.tool == "bash(npm *)" and r.permission == Permission.ALLOW
        )
        assert allow_count == 1

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

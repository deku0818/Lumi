"""执行模式策略 (mode_policy.py) 测试

覆盖 readonly / normal 内置模式 + 自定义模式注册。
"""

import pytest

from lumi.agents.permissions.mode_policy import (
    READONLY_POLICY,
    ModePolicy,
    check_policy,
    filter_tools_for_mode,
    get_policy,
    register_policy,
)

# ── get_policy / register_policy ──


class TestPolicyRegistry:
    def test_normal_returns_none(self):
        assert get_policy("normal") is None

    def test_readonly_returns_policy(self):
        p = get_policy("readonly")
        assert p is not None
        assert p.name == "readonly"

    def test_unknown_returns_none(self):
        assert get_policy("nonexistent_mode") is None

    def test_register_custom_policy(self):
        custom = ModePolicy(
            name="audit",
            label="Audit mode",
            allow_write=False,
        )
        register_policy("audit", custom)
        assert get_policy("audit") is custom
        # 清理
        from lumi.agents.permissions.mode_policy import _POLICIES

        del _POLICIES["audit"]


# ── check_policy: Readonly mode ──


class TestCheckPolicyReadonly:
    """Read-only mode 策略测试 — 所有写入一律拒绝"""

    policy = READONLY_POLICY

    # 只读 → 放行
    @pytest.mark.parametrize("tool_name", ["read", "glob", "grep"])
    def test_readonly_allowed(self, tool_name):
        assert check_policy(self.policy, tool_name, {}).allowed

    # ask / todos → 放行（只读）
    def test_ask_allowed(self):
        assert check_policy(self.policy, "ask", {}).allowed

    def test_todos_allowed(self):
        assert check_policy(self.policy, "todos", {}).allowed

    # cron 只读操作 → 放行
    def test_cron_list_allowed(self):
        assert check_policy(self.policy, "cron", {"operation": "list"}).allowed

    # cron 写入操作 → 拒绝
    def test_cron_create_blocked(self):
        r = check_policy(self.policy, "cron", {"operation": "create"})
        assert not r.allowed

    # 所有写入 → 拒绝（无 path_filter，plan 文件也不允许）
    def test_write_plan_file_blocked(self):
        r = check_policy(self.policy, "write", {"file_path": "~/.lumi/plans/x.md"})
        assert not r.allowed

    def test_edit_any_file_blocked(self):
        r = check_policy(self.policy, "edit", {"file_path": "any.py"})
        assert not r.allowed

    # bash 写入 → 拒绝
    def test_bash_mkdir_blocked(self):
        r = check_policy(self.policy, "bash", {"command": "mkdir /tmp/x"})
        assert not r.allowed

    # bash 只读 → 放行
    def test_bash_cat_allowed(self):
        assert check_policy(self.policy, "bash", {"command": "cat file.txt"}).allowed


# ── check_policy: Custom mode ──


class TestCheckPolicyCustom:
    """自定义策略测试"""

    def test_custom_no_write(self):
        """不允许写入的极简策略"""
        strict = ModePolicy(
            name="strict",
            label="Strict mode",
            allow_write=False,
        )
        assert check_policy(strict, "read", {}).allowed
        assert check_policy(strict, "ask", {}).allowed
        assert check_policy(strict, "todos", {}).allowed
        assert not check_policy(strict, "write", {"file_path": "x"}).allowed

    def test_custom_with_path_filter(self):
        """自定义路径过滤器"""
        docs_only = ModePolicy(
            name="docs",
            label="Docs mode",
            allow_write=False,
            path_filter=lambda p: p.endswith(".md"),
        )
        assert check_policy(docs_only, "write", {"file_path": "README.md"}).allowed
        assert not check_policy(docs_only, "write", {"file_path": "main.py"}).allowed

    def test_allow_write_mode(self):
        """allow_write=True 时不限制"""
        permissive = ModePolicy(
            name="permissive",
            label="Permissive mode",
            allow_write=True,
        )
        assert check_policy(permissive, "write", {"file_path": "x"}).allowed
        assert check_policy(permissive, "bash", {"command": "rm -rf /"}).allowed


# ── filter_tools_for_mode ──


class TestFilterToolsForMode:
    """子 Agent 工具过滤"""

    class FakeTool:
        def __init__(self, name: str):
            self.name = name

    def test_path_filter_keeps_readonly_and_write_tools(self):
        # 带 path_filter 的策略：写入工具保留，运行时再按路径放行
        docs = ModePolicy(
            name="docs",
            label="Docs mode",
            allow_write=False,
            path_filter=lambda p: p.endswith(".md"),
        )
        tools = [
            self.FakeTool(n) for n in ["read", "glob", "ask", "write", "edit", "bash"]
        ]
        filtered = filter_tools_for_mode(tools, docs)
        names = [t.name for t in filtered]
        assert "read" in names
        assert "glob" in names
        assert "ask" in names
        assert "bash" in names  # bash 保留（运行时动态判断）
        # write/edit 有 path_filter → 也保留（运行时检查路径）
        assert "write" in names
        assert "edit" in names

    def test_readonly_removes_write_tools(self):
        tools = [
            self.FakeTool(n)
            for n in ["read", "glob", "ask", "write", "edit", "bash", "todos"]
        ]
        filtered = filter_tools_for_mode(tools, READONLY_POLICY)
        names = [t.name for t in filtered]
        assert "read" in names
        assert "glob" in names
        assert "ask" in names
        assert "bash" in names  # bash 保留
        assert "todos" in names  # todos 现在是只读
        # write/edit 无 path_filter → 移除
        assert "write" not in names
        assert "edit" not in names

    def test_strict_mode_removes_write(self):
        strict = ModePolicy(
            name="strict",
            label="Strict",
            allow_write=False,
        )
        tools = [self.FakeTool(n) for n in ["read", "ask", "write", "todos", "bash"]]
        filtered = filter_tools_for_mode(tools, strict)
        names = [t.name for t in filtered]
        assert "read" in names
        assert "ask" in names
        assert "todos" in names
        assert "bash" in names  # bash 保留
        assert "write" not in names

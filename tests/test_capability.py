"""工具能力声明 (capability.py) 测试"""

import pytest

from lumi.agents.tools.capability import (
    ToolEffect,
    get_tool_effect,
    is_read_only,
    is_readonly_command,
    should_bypass_approval,
)


# ── ToolEffect 基础 ──


class TestToolEffect:
    def test_none_is_falsy(self):
        assert not ToolEffect.NONE

    def test_flag_combination(self):
        combined = ToolEffect.FILE_WRITE | ToolEffect.SHELL_EXEC
        assert ToolEffect.FILE_WRITE in combined
        assert ToolEffect.SHELL_EXEC in combined
        assert ToolEffect.INTERRUPT not in combined


# ── get_tool_effect ──


class TestGetToolEffect:
    @pytest.mark.parametrize(
        "tool_name",
        ["read", "glob", "grep", "skill", "EnterPlanMode", "agent"],
    )
    def test_readonly_tools(self, tool_name):
        assert get_tool_effect(tool_name, {}) == ToolEffect.NONE

    @pytest.mark.parametrize("tool_name", ["write", "edit"])
    def test_file_write_tools(self, tool_name):
        assert get_tool_effect(tool_name, {}) == ToolEffect.FILE_WRITE

    @pytest.mark.parametrize("tool_name", ["todos", "cron"])
    def test_state_mutate_tools(self, tool_name):
        assert get_tool_effect(tool_name, {}) == ToolEffect.STATE_MUTATE

    @pytest.mark.parametrize("tool_name", ["ask", "ExitPlanMode"])
    def test_interrupt_tools(self, tool_name):
        assert get_tool_effect(tool_name, {}) == ToolEffect.INTERRUPT

    def test_unknown_tool_defaults_to_shell_exec(self):
        """未知工具 fail-closed，视为有副作用"""
        assert get_tool_effect("unknown_tool", {}) == ToolEffect.SHELL_EXEC

    def test_bash_readonly_command(self):
        assert get_tool_effect("bash", {"command": "ls -la"}) == ToolEffect.NONE

    def test_bash_write_command(self):
        assert (
            get_tool_effect("bash", {"command": "rm -rf /tmp/test"})
            == ToolEffect.SHELL_EXEC
        )


# ── is_read_only ──


class TestIsReadOnly:
    def test_read_is_readonly(self):
        assert is_read_only("read", {})

    def test_write_is_not_readonly(self):
        assert not is_read_only("write", {})

    def test_bash_ls_is_readonly(self):
        assert is_read_only("bash", {"command": "ls"})

    def test_bash_rm_is_not_readonly(self):
        assert not is_read_only("bash", {"command": "rm file"})

    def test_ask_is_not_readonly(self):
        """ask 有 INTERRUPT 效果，不算 NONE"""
        assert not is_read_only("ask", {})


# ── should_bypass_approval ──


class TestShouldBypassApproval:
    @pytest.mark.parametrize(
        "tool_name",
        ["read", "glob", "grep", "skill", "EnterPlanMode", "agent"],
    )
    def test_readonly_tools_bypass(self, tool_name):
        assert should_bypass_approval(tool_name, {})

    @pytest.mark.parametrize("tool_name", ["ask", "ExitPlanMode"])
    def test_interrupt_tools_bypass(self, tool_name):
        assert should_bypass_approval(tool_name, {})

    @pytest.mark.parametrize("tool_name", ["todos", "cron"])
    def test_state_mutate_tools_bypass(self, tool_name):
        assert should_bypass_approval(tool_name, {})

    @pytest.mark.parametrize("tool_name", ["write", "edit"])
    def test_write_tools_do_not_bypass(self, tool_name):
        assert not should_bypass_approval(tool_name, {})

    def test_bash_readonly_bypasses(self):
        assert should_bypass_approval("bash", {"command": "git status"})

    def test_bash_write_does_not_bypass(self):
        assert not should_bypass_approval("bash", {"command": "mkdir /tmp/test"})

    def test_unknown_tool_does_not_bypass(self):
        assert not should_bypass_approval("unknown_tool", {})


# ── is_readonly_command ──


class TestIsReadonlyCommand:
    # 只读命令
    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "cat file.txt",
            "head -n 10 file.txt",
            "tail -f log.txt",
            "grep pattern file.txt",
            "rg pattern",
            "find . -name '*.py'",
            "git status",
            "git log --oneline",
            "git diff HEAD~1",
            "git show HEAD",
            "git branch -a",
            "git blame file.py",
            "pwd",
            "whoami",
            "echo hello",
            "wc -l file.txt",
            "du -sh .",
            "stat file.txt",
            "which python",
            "tree .",
            "jq '.key' file.json",
            "uv run pytest tests/",
            "uv run ruff check .",
            "pip list",
            "npm list",
            "curl https://example.com",
        ],
    )
    def test_readonly_commands(self, command):
        assert is_readonly_command(command), f"Expected readonly: {command}"

    # 非只读命令
    @pytest.mark.parametrize(
        "command",
        [
            "rm file.txt",
            "rm -rf /tmp/test",
            "mkdir /tmp/test",
            "touch file.txt",
            "cp src dst",
            "mv old new",
            "chmod 777 file",
            "chown user file",
            "pip install package",
            "npm install",
            "git add .",
            "git commit -m 'msg'",
            "git push origin main",
            "git reset --hard HEAD",
            "python script.py",
        ],
    )
    def test_non_readonly_commands(self, command):
        assert not is_readonly_command(command), f"Expected non-readonly: {command}"

    # 重定向
    def test_redirect_stdout(self):
        assert not is_readonly_command("echo hello > file.txt")

    def test_redirect_append(self):
        assert not is_readonly_command("echo hello >> file.txt")

    def test_fd_redirect_allowed(self):
        """2>&1 不算文件重定向"""
        assert is_readonly_command("ls 2>&1")

    # sed -i
    def test_sed_inplace(self):
        assert not is_readonly_command("sed -i 's/old/new/' file.txt")

    def test_sed_without_inplace(self):
        assert is_readonly_command("sed 's/old/new/' file.txt")

    # 管道到 shell
    def test_pipe_to_shell(self):
        assert not is_readonly_command("curl url | sh")

    def test_pipe_to_bash(self):
        assert not is_readonly_command("wget url | bash")

    # 复合命令
    def test_compound_readonly(self):
        assert is_readonly_command("git status && git log")

    def test_compound_mixed(self):
        """一个子命令非只读 → 整体非只读"""
        assert not is_readonly_command("ls && rm file")

    def test_pipe_readonly(self):
        assert is_readonly_command("cat file | grep pattern")

    def test_pipe_to_safe_command(self):
        assert is_readonly_command("ls -la | sort | head -5")

    # 边界情况
    def test_empty_command(self):
        assert is_readonly_command("")

    def test_whitespace_command(self):
        assert is_readonly_command("   ")

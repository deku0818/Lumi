"""工具能力声明 (capability.py) 测试"""

import pytest

from lumi.agents.tools.capability import (
    is_read_only,
    is_readonly_command,
    is_write_tool,
)

# ── is_write_tool ──


class TestIsWriteTool:
    @pytest.mark.parametrize(
        "tool_name",
        ["read", "glob", "grep", "skill", "agent"],
    )
    def test_readonly_tools(self, tool_name):
        assert not is_write_tool(tool_name, {})

    @pytest.mark.parametrize("tool_name", ["ask", "todos"])
    def test_ask_todos_are_readonly(self, tool_name):
        assert not is_write_tool(tool_name, {})

    @pytest.mark.parametrize("tool_name", ["write", "edit"])
    def test_write_tools(self, tool_name):
        assert is_write_tool(tool_name, {})

    def test_unknown_tool_is_write(self):
        """未知工具 fail-closed，视为写入"""
        assert is_write_tool("unknown_tool", {})

    def test_bash_readonly_command(self):
        assert not is_write_tool("bash", {"command": "ls -la"})

    def test_bash_write_command(self):
        assert is_write_tool("bash", {"command": "rm -rf /tmp/test"})

    # cron 按 operation 区分
    def test_cron_list_is_readonly(self):
        assert not is_write_tool("cron", {"operation": "list"})

    def test_cron_runs_is_readonly(self):
        assert not is_write_tool("cron", {"operation": "runs"})

    @pytest.mark.parametrize(
        "operation", ["create", "update", "delete", "run", "pause"]
    )
    def test_cron_write_operations(self, operation):
        assert is_write_tool("cron", {"operation": operation})

    def test_cron_no_operation_is_write(self):
        """无 operation 参数时 fail-closed"""
        assert is_write_tool("cron", {})


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

    def test_ask_is_readonly(self):
        assert is_read_only("ask", {})

    def test_todos_is_readonly(self):
        assert is_read_only("todos", {})


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

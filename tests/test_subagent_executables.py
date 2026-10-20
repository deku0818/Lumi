import pytest

from lumi.agents.runtime.subagents import executables


@pytest.fixture
def candidates(monkeypatch, tmp_path):
    available = set()
    monkeypatch.setattr(executables.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(
        executables.shutil, "which", lambda name: name if name in available else None
    )
    return available


def test_system_cli_takes_priority(candidates):
    candidates.add("codex")
    assert executables.resolve_cli("codex") == "codex"


def test_codex_desktop_is_found_without_parent_environment(candidates, monkeypatch):
    monkeypatch.setattr(executables.sys, "platform", "darwin")
    path = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    candidates.add(path)
    assert executables.resolve_cli("codex") == path


def test_known_desktop_environment_path(candidates, monkeypatch):
    monkeypatch.setenv("CODEX_CLI_PATH", "/custom-app/codex")
    candidates.add("/custom-app/codex")
    assert executables.resolve_cli("codex") == "/custom-app/codex"


def test_local_claude_install_without_shell_path(candidates, tmp_path):
    path = str(tmp_path / ".local/bin/claude")
    candidates.add(path)
    assert executables.resolve_cli("claude") == path


def test_remote_linux_does_not_search_mac_applications(candidates, monkeypatch):
    monkeypatch.setattr(executables.sys, "platform", "linux")
    candidates.add(
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    )
    with pytest.raises(RuntimeError, match="运行 Lumi 的机器"):
        executables.resolve_cli("codex")


def test_missing_cli_reports_actionable_error(candidates):
    with pytest.raises(RuntimeError, match="PATH"):
        executables.resolve_cli("claude")

import io
import json
import socket
import struct
import subprocess
import threading
import urllib.error
from pathlib import Path

import pytest

from lumi.remote import bootstrap as remote


def native_runtime(data, version="0.3.0"):
    return {
        "transport": "ssh-stdio",
        "protocol": 1,
        "version": version,
        "pid": 42,
        "executable": str(data / "remote-runtime/versions" / version / "bin/python"),
        "data_dir": str(data),
    }


def test_existing_instance_reuses_process_without_install(tmp_path, monkeypatch):
    instance = native_runtime(tmp_path)
    monkeypatch.setattr(remote, "host_service", lambda: "systemd")
    monkeypatch.setattr(remote, "runtime_info", lambda _: instance)
    monkeypatch.setattr(
        remote, "install_uv", lambda _: pytest.fail("must not reinstall")
    )
    assert (
        remote.bootstrap(
            {"action": "connect", "data_dir": str(tmp_path), "version": "0.3.0"}
        )
        == instance
    )
    assert "port" not in instance and "token" not in instance


def test_default_data_directory_remains_lumi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = []
    monkeypatch.setattr(remote, "runtime_info", lambda data: seen.append(data))
    result = remote.bootstrap({"action": "probe", "version": "0.3.0"})
    assert seen == [tmp_path / ".lumi"]
    assert result["data_dir"] == str(tmp_path / ".lumi")
    assert not (tmp_path / ".lumi").exists(), "Probing must not create data"


@pytest.mark.parametrize("version", ["0.2.138", "0.3.1", "0.4.0"])
def test_probe_reports_different_version_without_changing_service(
    tmp_path, monkeypatch, version
):
    monkeypatch.setattr(
        remote, "runtime_info", lambda _: native_runtime(tmp_path, version)
    )
    monkeypatch.setattr(
        remote, "host_service", lambda: pytest.fail("probe must be read-only")
    )
    result = remote.bootstrap(
        {"action": "probe", "data_dir": str(tmp_path), "version": "0.3.0"}
    )
    assert result["runtime"]["version"] == version
    assert not result["installed"]
    assert not tmp_path.joinpath("remote-runtime").exists()


@pytest.mark.parametrize(
    "files,available",
    [([], False), ([{"yanked": True}], False), ([{"yanked": False}], True)],
)
def test_release_requires_available_distribution(monkeypatch, files, available):
    def fetch(url, timeout):
        assert url == "https://pypi.org/pypi/lumi-harness/0.3.0/json"
        return io.BytesIO(json.dumps({"urls": files}).encode())

    monkeypatch.setattr(remote.urllib.request, "urlopen", fetch)
    if available:
        remote.require_release("0.3.0")
    else:
        with pytest.raises(RuntimeError, match="没有可安装"):
            remote.require_release("0.3.0")


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://pypi.org", 404, "Missing", {}, None),
        urllib.error.HTTPError("https://pypi.org", 503, "Unavailable", {}, None),
        urllib.error.URLError("DNS failed"),
        TimeoutError("request timed out"),
    ],
)
def test_release_errors_are_returned_unchanged(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(remote.urllib.request, "urlopen", fail)
    with pytest.raises(type(error)) as caught:
        remote.require_release("0.3.0")
    assert caught.value is error


def test_command_failure_returns_complete_original_error(monkeypatch):
    stderr = "Failed to download dependency\nConnection reset by peer"

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr=stderr)

    monkeypatch.setattr(remote.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as caught:
        remote.run(["uv", "pip", "install"])
    assert str(caught.value) == stderr


@pytest.mark.parametrize(
    "actual,code,expected", [("0.3.0", 0, True), ("0.3.1", 0, False), ("", 1, False)]
)
def test_installed_checks_actual_package_version(
    tmp_path, monkeypatch, actual, code, expected
):
    executable = tmp_path / "bin/python"
    executable.parent.mkdir()
    executable.touch()
    executable.with_name("lumi").touch()
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, code, actual, ""),
    )
    assert not remote.installed(executable, "0.3.0"), (
        "partial installs must not be reused"
    )
    executable.parent.parent.joinpath(".installed").touch()
    assert remote.installed(executable, "0.3.0") is expected


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    old = native_runtime(tmp_path, "0.3.0")
    state = {"runtime": old, "installed": False, "events": []}
    monkeypatch.setattr(remote, "runtime_info", lambda _: state["runtime"])
    monkeypatch.setattr(remote, "installed", lambda *args: state["installed"])
    monkeypatch.setattr(remote, "host_service", lambda: "systemd")
    monkeypatch.setattr(
        remote,
        "require_release",
        lambda _: pytest.fail("runtime must not precheck the PyPI JSON API"),
    )
    monkeypatch.setattr(remote, "install_uv", lambda _: Path("/fixture/uv"))
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""),
    )

    def run(args, **kwargs):
        assert state["runtime"] == old, "installation must leave the old daemon running"
        state["events"].append(args)
        if args[1] == "venv":
            Path(args[-1]).mkdir(parents=True)
        if args[1:3] == ["pip", "install"]:
            state["installed"] = True

    def stop(*args):
        state["events"].append("stop")
        state["runtime"] = None

    def start(root, data, executable, host):
        version = executable.parent.parent.name
        state["events"].append("start:" + version)
        state["runtime"] = native_runtime(data, version)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "stop_service", stop)
    monkeypatch.setattr(remote, "start_service", start)
    state["request"] = {
        "action": "connect",
        "data_dir": str(tmp_path),
        "version": "0.3.1",
    }
    return state


def test_update_installs_exact_pypi_version_before_restarting(deployment, capsys):
    result = remote.bootstrap(deployment["request"])
    events = deployment["events"]
    install = next(
        event for event in events if isinstance(event, list) and "install" in event
    )
    assert install[-3:] == [
        "--index-url",
        "https://pypi.org/simple",
        "lumi-harness==0.3.1",
    ]
    assert events.index(install) < events.index("stop")
    assert result["version"] == "0.3.1"
    assert events[-2:] == ["stop", "start:0.3.1"]
    phases = [
        json.loads(line.removeprefix("LUMI_STATUS:"))["phase"]
        for line in capsys.readouterr().out.splitlines()
    ]
    assert phases == ["updating", "restarting"]


@pytest.mark.parametrize(
    "current,target",
    [("0.3.10", "0.3.9"), ("0.10.0", "0.9.9"), ("1.0.0", "0.99.99")],
)
def test_newer_remote_is_not_downgraded(tmp_path, monkeypatch, current, target):
    monkeypatch.setattr(
        remote, "runtime_info", lambda _: native_runtime(tmp_path, current)
    )
    monkeypatch.setattr(
        remote, "host_service", lambda: pytest.fail("must not alter newer remote")
    )
    with pytest.raises(RuntimeError, match="请先更新桌面"):
        remote.bootstrap(
            {"action": "connect", "version": target, "data_dir": str(tmp_path)}
        )
    assert not (tmp_path / "remote-runtime").exists()


def test_version_is_rechecked_after_taking_bootstrap_lock(deployment, monkeypatch):
    observations = iter(
        [
            native_runtime(Path(deployment["request"]["data_dir"]), "0.3.0"),
            native_runtime(Path(deployment["request"]["data_dir"]), "0.3.2"),
        ]
    )
    monkeypatch.setattr(remote, "runtime_info", lambda _: next(observations))
    with pytest.raises(RuntimeError, match="请先更新桌面"):
        remote.bootstrap(deployment["request"])
    assert not deployment["events"], "a stale initial probe must not permit a downgrade"


def test_old_desktop_reconnects_do_not_restart_new_remote(deployment):
    deployment["installed"] = True
    request = deployment["request"]
    assert remote.bootstrap(request)["version"] == "0.3.1"
    for _ in range(3):
        with pytest.raises(RuntimeError, match="请先更新桌面"):
            remote.bootstrap({**request, "version": "0.3.0"})
        assert remote.bootstrap(request)["version"] == "0.3.1"
    assert deployment["events"] == ["stop", "start:0.3.1"]


def test_numeric_patch_upgrade_is_allowed(deployment):
    deployment["installed"] = True
    deployment["runtime"]["version"] = "0.3.9"
    result = remote.bootstrap({**deployment["request"], "version": "0.3.10"})
    assert result["version"] == "0.3.10"
    assert deployment["events"] == ["stop", "start:0.3.10"]


def test_nonrelease_version_mismatch_does_not_switch_services(deployment):
    deployment["runtime"]["version"] = "0.3.1rc1"
    with pytest.raises(RuntimeError, match="无法自动切换"):
        remote.bootstrap(deployment["request"])
    assert not deployment["events"]


def test_cached_version_switch_does_not_require_network(deployment, monkeypatch):
    deployment["installed"] = True
    monkeypatch.setattr(
        remote,
        "require_release",
        lambda _: pytest.fail("cached version needs no network"),
    )
    assert remote.bootstrap(deployment["request"])["version"] == "0.3.1"
    assert deployment["events"] == ["stop", "start:0.3.1"]


def test_failed_install_keeps_old_service(deployment, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("No space left on device")

    monkeypatch.setattr(remote, "run", fail)
    with pytest.raises(RuntimeError, match="No space left on device"):
        remote.bootstrap(deployment["request"])
    assert deployment["runtime"]["version"] == "0.3.0"
    assert "stop" not in deployment["events"]


def test_package_resolution_failure_returns_uv_error_and_keeps_old_service(
    deployment, monkeypatch
):
    run = remote.run
    diagnostic = (
        "No solution found: lumi-harness==0.3.1 was not found in the package registry"
    )

    def install(args, **kwargs):
        if args[1:3] == ["pip", "install"]:
            raise RuntimeError(diagnostic)
        return run(args, **kwargs)

    monkeypatch.setattr(remote, "run", install)
    with pytest.raises(RuntimeError) as caught:
        remote.bootstrap(deployment["request"])
    assert str(caught.value) == diagnostic
    assert deployment["runtime"]["version"] == "0.3.0"
    assert "stop" not in deployment["events"]


@pytest.mark.parametrize("recovery_fails", [False, True])
def test_failed_start_recovers_previous_service(
    deployment, monkeypatch, recovery_fails
):
    deployment["installed"] = True

    def start(root, data, executable, host):
        version = executable.parent.parent.name
        deployment["events"].append("start:" + version)
        if version == "0.3.1" or recovery_fails:
            raise RuntimeError("fixture service error")
        deployment["runtime"] = native_runtime(data, version)

    monkeypatch.setattr(remote, "start_service", start)
    message = "恢复旧服务失败" if recovery_fails else "已恢复"
    with pytest.raises(RuntimeError, match=message) as caught:
        remote.bootstrap(deployment["request"])
    assert "fixture service error" in str(caught.value)
    assert deployment["events"] == ["stop", "start:0.3.1", "stop", "start:0.3.0"]
    if not recovery_fails:
        assert deployment["runtime"]["version"] == "0.3.0"


@pytest.mark.parametrize("host", ["systemd", "launchd"])
def test_stop_service_waits_for_daemon_to_release_instance_lock(
    tmp_path, monkeypatch, host
):
    import fcntl

    root = tmp_path / "remote-runtime"
    root.mkdir()
    commands = []
    daemon_lock = (root / "instance.lock").open("a")
    fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(remote, "run", lambda args: commands.append(args))
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
    )
    waits = []

    def exit_daemon(_):
        waits.append(True)
        daemon_lock.close()

    monkeypatch.setattr(remote.time, "sleep", exit_daemon)
    try:
        remote.stop_service(root, host)
        assert waits == [True], "service manager return does not prove daemon exit"
        assert commands[0][:2] == (
            ["systemctl", "--user"] if host == "systemd" else ["launchctl", "bootout"]
        )
    finally:
        daemon_lock.close()


def test_startup_failure_returns_service_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "runtime_info", lambda _: None)
    monkeypatch.setattr(remote.time, "sleep", lambda _: None)
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    diagnostic = "ModuleNotFoundError: fixture_dependency"
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, diagnostic, ""),
    )
    with pytest.raises(RuntimeError, match=diagnostic):
        remote.wait_ready(tmp_path, "0.3.1")


@pytest.mark.parametrize(
    "content", [b"short log", b"old log\n" * 200000 + "启动失败：依赖缺失".encode()]
)
def test_startup_failure_reads_only_log_tail(tmp_path, monkeypatch, content):
    log = tmp_path / "remote-runtime/service.log"
    log.parent.mkdir()
    log.touch()
    reads = []

    class BoundedLog(io.BytesIO):
        def read(self, size=-1):
            reads.append((self.tell(), size))
            assert 0 <= size <= 8000, (
                "log reads must stay bounded regardless of file size"
            )
            return super().read(size)

    open_path = Path.open

    def open_log(path, *args, **kwargs):
        if path == log:
            return BoundedLog(content)
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_log)
    monkeypatch.setattr(remote, "runtime_info", lambda _: None)
    monkeypatch.setattr(remote.time, "sleep", lambda _: None)
    monkeypatch.setattr(remote.platform, "system", lambda: "Darwin")
    with pytest.raises(RuntimeError) as caught:
        remote.wait_ready(tmp_path, "0.3.1")
    assert str(caught.value) == "远端 Lumi 未能启动。\n" + content[-8000:].decode(
        "utf-8", errors="replace"
    )
    assert reads == [(max(0, len(content) - 8000), 8000)]


def test_probe_reads_fragmented_private_socket_hello(tmp_path):
    # Keep the AF_UNIX path below macOS's 104-byte limit.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="lumi-wire-", dir="/tmp") as folder:
        data = Path(folder)
        root = data / "remote-runtime"
        root.mkdir()
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(root / "agent.sock"))
        server.listen()
        info = native_runtime(data)
        payload = json.dumps(info).encode()

        def answer():
            with server.accept()[0] as connection:
                for byte in struct.pack("!I", len(payload)) + payload:
                    connection.sendall(bytes([byte]))

        worker = threading.Thread(target=answer)
        worker.start()
        try:
            assert remote.runtime_info(data) == info
        finally:
            worker.join(timeout=5)
            server.close()


def test_private_descriptors_are_atomic_and_not_world_readable(tmp_path):
    target = tmp_path / "instance.json"
    remote.write_private(target, '{"generation": 1}')
    remote.write_private(target, '{"generation": 2}')
    assert json.loads(target.read_text()) == {"generation": 2}
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_linux_requires_linger_before_starting_any_service(monkeypatch):
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    calls = []

    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Linger=no\n", "")

    monkeypatch.setattr(remote, "run", run)
    with pytest.raises(RuntimeError, match="linger"):
        remote.host_service()
    assert len(calls) == 1


def test_service_uses_resident_daemon_without_network_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    root = tmp_path / "isolated data" / "remote-runtime"
    root.mkdir(parents=True)
    commands = []
    monkeypatch.setattr(remote, "run", lambda args: commands.append(args))
    executable = root / "versions/0.3.0/bin/python"
    remote.start_service(root, root.parent, executable, "systemd")
    name, unit = remote.service_paths(root, "systemd")
    content = unit.read_text()
    assert "WorkingDirectory=" + str(root.parent) in content
    assert '"-m" "lumi.remote.daemon" "--data-dir"' in content
    assert str(executable) in content
    assert not any(
        arg in content
        for arg in (
            "--port",
            "--token",
            "serve",
            "EnvironmentFile",
            "--exit-with-parent",
        )
    )
    assert name.startswith("lumi-remote-")
    assert commands[-1] == ["systemctl", "--user", "enable", "--now", name]
    assert unit.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("loaded", [False, True])
def test_launchd_arguments_use_socket_daemon_and_keep_data_directory(
    tmp_path, monkeypatch, loaded
):
    import plistlib

    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    root = tmp_path / "data with spaces" / "remote-runtime"
    root.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(remote, "run", lambda args: calls.append(args))
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0 if loaded else 1),
    )
    executable = root / "versions/0.3.0/bin/python"
    remote.start_service(root, root.parent, executable, "launchd")
    name, path = remote.service_paths(root, "launchd")
    info = plistlib.loads(path.read_bytes())
    assert info["ProgramArguments"] == [
        str(executable),
        "-m",
        "lumi.remote.daemon",
        "--data-dir",
        str(root.parent),
    ]
    assert info["EnvironmentVariables"]["LUMI_CONFIG_DIR"] == str(root.parent)
    assert calls == (
        [["launchctl", "kickstart", f"gui/{remote.os.getuid()}/" + name]]
        if loaded
        else [["launchctl", "bootstrap", f"gui/{remote.os.getuid()}", str(path)]]
    )
    assert info["Label"] == name

import json
import socket
import struct
import subprocess
import threading
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
def test_different_runtime_version_is_not_implicitly_upgraded(
    tmp_path, monkeypatch, version
):
    monkeypatch.setattr(
        remote, "runtime_info", lambda _: native_runtime(tmp_path, version)
    )
    with pytest.raises(RuntimeError, match="版本不一致"):
        remote.bootstrap(
            {"action": "probe", "data_dir": str(tmp_path), "version": "0.3.0"}
        )


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

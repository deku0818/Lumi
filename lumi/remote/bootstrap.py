"""Prepare a native remote runtime; also sent verbatim to remote Python over SSH."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path

PROTOCOL = 1


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=True, **kwargs
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.strip() or str(error)) from error


def installed(executable: Path, version: str) -> bool:
    if (
        not executable.is_file()
        or not (executable.parent / "lumi").is_file()
        or not (executable.parent.parent / ".installed").is_file()
    ):
        return False
    result = subprocess.run(
        [
            str(executable),
            "-c",
            "from importlib.metadata import version; print(version('lumi-harness'))",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == version


def require_release(version: str) -> None:
    """Check availability before publishing the desktop release."""
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/lumi-harness/{version}/json", timeout=30
    ) as response:
        files = json.load(response)["urls"]
    if not any(not file["yanked"] for file in files):
        raise RuntimeError(f"PyPI 上没有可安装的 lumi-harness=={version} 版本。")


def check_update(ready: dict | None, version: str) -> None:
    if not ready or ready["version"] == version:
        return
    current_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ready["version"])
    target_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not current_match or not target_match:
        raise RuntimeError(
            f"远端版本 {ready['version']} 与桌面版本 {version} 不匹配，无法自动切换。"
        )
    current_release = tuple(map(int, current_match.groups()))
    target_release = tuple(map(int, target_match.groups()))
    if current_release > target_release:
        raise RuntimeError(
            f"远端 Lumi 版本 {ready['version']} 高于当前桌面版本 {version}，请先更新桌面。"
        )


def progress(phase: str, data: Path) -> None:
    print(
        "LUMI_STATUS:" + json.dumps({"phase": phase, "dataDir": str(data)}), flush=True
    )


def runtime_info(data: Path) -> dict | None:
    def receive(connection: socket.socket, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = connection.recv(size - len(result))
            if not chunk:
                raise ConnectionError("Remote runtime closed during handshake")
            result.extend(chunk)
        return bytes(result)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(data / "remote-runtime/agent.sock"))
            size = struct.unpack("!I", receive(connection, 4))[0]
            if not 0 < size <= 65536:
                raise RuntimeError("Invalid remote runtime handshake")
            info = json.loads(receive(connection, size))
    except (OSError, ValueError):
        return None
    if info.get("transport") != "ssh-stdio" or info.get("protocol") != PROTOCOL:
        raise RuntimeError("远端运行协议与当前桌面不一致，请更新远端 Lumi。")
    return info


def host_service() -> str:
    if platform.system() == "Linux":
        result = run(["loginctl", "show-user", str(os.getuid()), "-p", "Linger"])
        if "Linger=yes" not in result.stdout:
            raise RuntimeError(
                "远端用户尚未启用 linger，无法保证退出 SSH 后服务存活。"
                "请在目标机器为当前用户启用 loginctl enable-linger 后重试。"
            )
        if not os.environ.get("XDG_RUNTIME_DIR"):
            runtime = run(
                [
                    "loginctl",
                    "show-user",
                    str(os.getuid()),
                    "-p",
                    "RuntimePath",
                    "--value",
                ]
            ).stdout.strip()
            runtime_path = Path(runtime)
            if (
                not runtime
                or not runtime_path.is_dir()
                or runtime_path.stat().st_uid != os.getuid()
            ):
                raise RuntimeError("无法定位当前用户的 systemd 运行目录。")
            os.environ["XDG_RUNTIME_DIR"] = runtime
            os.environ.setdefault(
                "DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(runtime_path / "bus")
            )
        run(["systemctl", "--user", "show-environment"])
        return "systemd"
    if platform.system() == "Darwin":
        return "launchd"
    raise RuntimeError("远端暂支持 Linux（systemd 用户服务）和 macOS（launchd）。")


def write_private(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    os.replace(temporary, path)


def install_uv(root: Path) -> Path:
    for existing in (
        shutil.which("uv"),
        Path.home() / ".local/bin/uv",
        root / "tools/uv",
    ):
        if existing and Path(existing).is_file():
            return Path(existing)
    target = root / "tools"
    target.mkdir(mode=0o700, exist_ok=True)
    env = {**os.environ, "UV_INSTALL_DIR": str(target), "UV_NO_MODIFY_PATH": "1"}
    with urllib.request.urlopen(
        "https://astral.sh/uv/install.sh", timeout=30
    ) as response:
        script = response.read().decode()
    run(
        ["sh"],
        input=script,
        env=env,
        timeout=180,
    )
    return target / "uv"


def install_version(root: Path, executable: Path, version: str) -> None:
    uv = install_uv(root)
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(root / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
    }
    version_dir = executable.parent.parent
    if not executable.exists():
        run([str(uv), "venv", "--python", "3.12", str(version_dir)], env=env)
    run(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            str(executable),
            "--index-url",
            "https://pypi.org/simple",
            f"lumi-harness=={version}",
        ],
        env=env,
    )
    write_private(version_dir / ".installed", version)
    if not installed(executable, version):
        raise RuntimeError("远端安装的 Lumi 版本不正确，请重试。")


def service_paths(root: Path, host: str) -> tuple[str, Path]:
    name = "lumi-remote-" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    if host == "systemd":
        return name, Path.home() / ".config/systemd/user" / f"{name}.service"
    return name, Path.home() / "Library/LaunchAgents" / f"{name}.plist"


def start_service(root: Path, data: Path, executable: Path, host: str) -> None:
    name, path = service_paths(root, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [str(executable), "-m", "lumi.remote.daemon", "--data-dir", str(data)]
    if host == "systemd":

        def quote(value: str) -> str:
            return (
                '"'
                + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
                + '"'
            )

        write_private(
            path,
            "\n".join(
                [
                    "[Unit]",
                    "Description=Lumi resident SSH agent",
                    "[Service]",
                    "ExecStart=" + " ".join(quote(arg) for arg in args),
                    "WorkingDirectory=" + str(data).replace("%", "%%"),
                    "Environment=" + quote("LUMI_CONFIG_DIR=" + str(data)),
                    "Environment=" + quote("PATH=" + os.environ.get("PATH", "")),
                    "Restart=on-failure",
                    "RestartSec=3",
                    "[Install]",
                    "WantedBy=default.target",
                    "",
                ]
            ),
        )
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", name])
    else:
        import plistlib

        write_private(
            path,
            plistlib.dumps(
                {
                    "Label": name,
                    "ProgramArguments": args,
                    "RunAtLoad": True,
                    "KeepAlive": {"SuccessfulExit": False},
                    "WorkingDirectory": str(data),
                    "EnvironmentVariables": {
                        "LUMI_CONFIG_DIR": str(data),
                        "PATH": os.environ.get("PATH", ""),
                    },
                    "StandardOutPath": str(root / "service.log"),
                    "StandardErrorPath": str(root / "service.log"),
                }
            ).decode(),
        )
        domain = f"gui/{os.getuid()}"
        loaded = (
            subprocess.run(
                ["launchctl", "print", domain + "/" + name], capture_output=True
            ).returncode
            == 0
        )
        if loaded:
            run(["launchctl", "kickstart", domain + "/" + name])
        else:
            run(["launchctl", "bootstrap", domain, str(path)])


def stop_service(root: Path, host: str) -> None:
    name, _ = service_paths(root, host)
    if host == "systemd":
        run(["systemctl", "--user", "stop", name])
    else:
        target = f"gui/{os.getuid()}/" + name
        status = subprocess.run(["launchctl", "print", target], capture_output=True)
        if status.returncode == 0:
            run(["launchctl", "bootout", target])
    import fcntl

    with (root / "instance.lock").open("a") as lock:
        for _ in range(90):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                time.sleep(1)
    raise RuntimeError("远端旧版本尚未退出，请稍后重试。")


def wait_ready(data: Path, version: str) -> dict:
    for _ in range(90):
        ready = runtime_info(data)
        if ready and ready["version"] == version:
            return ready
        time.sleep(1)
    root = data / "remote-runtime"
    if platform.system() == "Linux":
        name, _ = service_paths(root, "systemd")
        logs = subprocess.run(
            ["journalctl", "--user", "-u", name, "-n", "40", "--no-pager"],
            capture_output=True,
            text=True,
        )
        details = logs.stdout or logs.stderr
    else:
        log = root / "service.log"
        details = ""
        if log.is_file():
            with log.open("rb") as source:
                end = source.seek(0, os.SEEK_END)
                source.seek(max(0, end - 8000))
                details = source.read(8000).decode("utf-8", errors="replace")
    raise RuntimeError("远端 Lumi 未能启动。\n" + details[-8000:])


def bootstrap(request: dict) -> dict:
    data = Path(request.get("data_dir") or "~/.lumi").expanduser().resolve()
    if any(char in str(data) for char in "\r\n\x00"):
        raise ValueError("Invalid Lumi data directory")
    version = request["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+[a-z0-9.+-]*", version):
        raise ValueError("Invalid Lumi version")
    root = data / "remote-runtime"
    executable = root / "versions" / version / "bin/python"
    ready = runtime_info(data)
    if request["action"] == "probe":
        return {
            "data_dir": str(data),
            "installed": installed(executable, version),
            "managed": (root / "versions").is_dir(),
            "runtime": ready,
        }
    check_update(ready, version)
    if ready and ready["version"] == version:
        return ready
    host = host_service()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    data.mkdir(parents=True, exist_ok=True)
    import fcntl

    with (root / "bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ready = runtime_info(data)
        check_update(ready, version)
        if ready and ready["version"] == version:
            return ready
        if not installed(executable, version):
            progress("updating" if ready else "installing", data)
            install_version(root, executable, version)
        name, _ = service_paths(root, host)
        if (
            not ready
            and host == "systemd"
            and subprocess.run(
                ["systemctl", "--user", "is-active", name], capture_output=True
            ).returncode
            == 0
        ):
            raise RuntimeError("远端 Lumi 正在运行但未完成握手，请检查此实例日志。")
        progress("restarting" if ready else "starting", data)
        try:
            if ready:
                stop_service(root, host)
            start_service(root, data, executable, host)
            return wait_ready(data, version)
        except Exception as error:
            if not ready:
                raise
            try:
                stop_service(root, host)
                start_service(root, data, Path(ready["executable"]), host)
                wait_ready(data, ready["version"])
            except Exception as recovery_error:
                raise RuntimeError(
                    f"远端更新失败：{error}\n恢复旧服务失败：{recovery_error}"
                ) from error
            raise RuntimeError(f"远端更新失败，已恢复旧服务：{error}") from error

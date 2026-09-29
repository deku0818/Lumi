"""桌面 sidecar 退出：经 stdin 收到 shutdown 走优雅停机（lifespan 收尾），而不是被硬杀。"""

import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="子进程 + 端口探测")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_serve(tmp_path) -> subprocess.Popen:
    port = _free_port()
    env = {**os.environ, "LUMI_CONFIG_DIR": str(tmp_path), "LUMI_TOKEN": "t"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lumi.cli",
            "serve",
            "--port",
            str(port),
            "--exit-with-parent",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc
        time.sleep(0.2)
    proc.kill()
    pytest.fail("serve 未在 60s 内起来")


def test_stdin_shutdown_line_stops_gracefully(tmp_path):
    # 回归：Electron 退出时只发 SIGTERM 就走，stdin 一断 sidecar 立刻 os._exit——在跑的轮
    # 来不及 drain、checkpoint 停在半截。现在先经 stdin 请求停机，等它走完 lifespan
    proc = _start_serve(tmp_path)
    try:
        # stdin 保持打开（同 Electron：等 sidecar 退出后自己才退），只发停机请求
        proc.stdin.write(b"shutdown\n")
        proc.stdin.flush()
        proc.wait(timeout=20)
        out = proc.stdout.read()
    finally:
        proc.kill()
    assert proc.returncode == 0
    assert b"Application shutdown complete" in out


def test_stdin_eof_still_exits_immediately(tmp_path):
    # 父进程崩溃 / 被强杀（管道被 OS 关闭）：不等停机，立即退出，免得孤儿抢 checkpoint 库
    proc = _start_serve(tmp_path)
    try:
        proc.stdin.close()
        proc.wait(timeout=10)
    finally:
        proc.kill()
    assert proc.returncode == 0
